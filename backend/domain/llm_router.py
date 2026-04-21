"""LLM-based specialist router.

Replaces the cosine-similarity router with a small, fast LLM that reads the
user's query (plus short conversation context) and picks one or more
specialists. Uses the same ``GUARDRAIL_MODEL`` pool as the input guardrail —
gpt-4.1-nano class latency (~200-400ms) — so adding it costs about as much
as the guardrail step already costs.

Routing contract returned to ``supervisor_agent.route_request`` is identical
to the old cosine path so no downstream node has to change:

    action ∈ {"delegate", "multi_delegate", "clarify", "out_of_scope", "direct"}
    agents: ordered list of specialist ids (len 1 or 2)
    confidence: 0..1 (for logging / threshold decisions)
    reason: short human-readable string
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from core.llm import get_guardrail_model
from domain.config.supervisor_routing import SPECIALIST_ROUTING_PROFILES

log = logging.getLogger(__name__)


RouteAction = Literal["delegate", "multi_delegate", "clarify", "out_of_scope", "direct"]
AgentId = Literal["hr", "finance", "admin", "it"]


class RouteDecision(BaseModel):
    """Structured output from the routing classifier."""

    action: RouteAction = Field(
        description=(
            "Routing decision. 'delegate' = exactly one specialist is clearly correct. "
            "'multi_delegate' = two specialists could plausibly help and you want both "
            "to be consulted in parallel. 'clarify' = the query is too vague to choose "
            "confidently and the user should be asked to pick a department. "
            "'out_of_scope' = the query is not something any specialist can answer. "
            "'direct' = the supervisor should answer this itself (platform help, "
            "greetings, meta questions)."
        )
    )
    agents: list[AgentId] = Field(
        default_factory=list,
        description=(
            "Specialist ids relevant to this query, ordered from most-relevant first. "
            "Length 1 for 'delegate', length 2 for 'multi_delegate' or 'clarify'. "
            "Leave empty for 'out_of_scope' or 'direct'."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How confident you are in the top agent. 0.0 = guessing, 1.0 = certain.",
    )
    reason: str = Field(
        default="",
        description="One short sentence explaining the routing decision.",
    )


def _profile_block(agent_id: str) -> str:
    """Render one specialist's description + examples for the router prompt."""
    profile = SPECIALIST_ROUTING_PROFILES[agent_id]
    display = profile["display_name"]
    description = profile["description"]
    # Use up to 6 examples to keep the prompt small.
    examples = list(profile.get("examples") or [])[:6]
    examples_block = "\n".join(f"    - {ex}" for ex in examples) if examples else "    (no examples)"
    return (
        f"{agent_id} ({display}):\n"
        f"  Description: {description}\n"
        f"  Example questions this specialist handles:\n{examples_block}"
    )


def _build_router_prompt() -> str:
    """Build the router system prompt from current specialist profiles."""
    specialist_blocks = "\n\n".join(
        _profile_block(agent_id) for agent_id in SPECIALIST_ROUTING_PROFILES.keys()
    )
    return f"""You are the router for Workmate AI, SLTMobitel's internal workplace assistant.

Your job: given a user message (plus optional short conversation history), pick which specialist should answer.

Available specialists:

{specialist_blocks}

Routing rules:

1. If the query clearly belongs to exactly ONE specialist's domain, return `action="delegate"` with that one agent in `agents`. This is the common case — prefer it whenever you can.

2. If the query legitimately spans TWO specialists (for example: a question that has both an HR policy aspect AND a Finance approval aspect), return `action="multi_delegate"` with both agents in order of relevance. Do not fan out just because a query is ambiguous — only when a complete answer truly requires both.

3. If the query is vague or could plausibly be about several specialists and you cannot pick confidently, return `action="clarify"` with the 2 most likely agents. The user will then be asked to choose.

4. If the query is clearly outside every specialist's scope (personal opinions, general-world trivia, jokes, competitor products, topics unrelated to any SLTMobitel internal function), return `action="out_of_scope"` with empty agents.

5. If the query is platform/meta (greetings, "what can you do", "which agent handles X", thanks, goodbyes) and no specialist needs to answer, return `action="direct"` with empty agents.

Matching hints (use these when relevant):
- Acronyms like MDRC, CDRTF, CDRAC, PDRC, BAAC, VAT, WHT, SLFRS, AP Invoice, GRN, CAPEX → Finance.
- Acronyms like EPF, ETF, Agrahara, TDC education loan, VOP, KPI, SRPS → HR.
- Acronyms like VPN, VDI, MFA, DLP, WAF, ERP outage, CRM outage → IT.
- Acronyms like FAFA, IDF, SPD, UPS; named committees/bodies like Transport Management Section, Property Management Team, Facility Management Section, Security Section → Admin.
- Physical things (vehicles, parking, visitor passes, gate passes, office building, AC, generator) → Admin.
- Money movement, invoices, payments, tax, approvals of financial limits → Finance.
- Employee personal status (leave, loans, medical benefits, grievance, performance review) → HR.
- Devices, accounts, passwords, network, software access → IT.

Important:
- Use the retrieved entity in the query (acronym, named committee, product name) as the strongest signal. If the query names an entity that appears in exactly one specialist's description or examples, route there with high confidence.
- Do NOT default to HR when unsure — HR is not a catch-all. Use `clarify` or `out_of_scope` instead.
- Short follow-up messages like "how about that?", "can I apply?", "how much?" should usually stick to the most recent specialist if one is provided in the history.
- Set `confidence` honestly. 0.9+ = entity/keyword match is explicit. 0.6-0.8 = clear domain fit but no distinctive keyword. <0.5 = guessing — consider `clarify` instead.
"""


# ── Singleton router LLM ────────────────────────────────────────────────
_router_llm = None


def _get_router():
    """Lazy-initialize the router LLM with structured output."""
    global _router_llm
    if _router_llm is None:
        base_model = get_guardrail_model()
        _router_llm = base_model.with_structured_output(RouteDecision)
    return _router_llm


def _format_history_tail(history_tail: Optional[list[dict[str, str]]]) -> str:
    """Render the last few turns as plain text for the router prompt."""
    if not history_tail:
        return ""
    lines: list[str] = []
    for turn in history_tail:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role.upper()}: {content[:400]}")
    return "\n".join(lines)


async def classify_route(
    query: str,
    *,
    last_specialist_agent: Optional[str] = None,
    history_tail: Optional[list[dict[str, str]]] = None,
) -> RouteDecision:
    """Ask the router LLM which specialist(s) should handle ``query``.

    Fails safe: on classifier error, returns a ``clarify`` decision over all
    four specialists so the user is asked instead of being silently misrouted.
    """
    try:
        router = _get_router()

        user_parts: list[str] = []
        if last_specialist_agent:
            user_parts.append(
                f"Most recent specialist in this thread: {last_specialist_agent}. "
                f"Short follow-ups should usually stick to this agent."
            )
        history_block = _format_history_tail(history_tail)
        if history_block:
            user_parts.append(f"Recent conversation:\n{history_block}")
        user_parts.append(f"Current user message:\n{query}")

        user_prompt = "\n\n".join(user_parts)

        decision: RouteDecision = await router.ainvoke(
            [
                {"role": "system", "content": _build_router_prompt()},
                {"role": "user", "content": user_prompt},
            ]
        )

        # Normalize: trim any stray agents the model invented (shouldn't happen
        # due to Literal constraint, but belt-and-braces).
        decision.agents = [a for a in decision.agents if a in SPECIALIST_ROUTING_PROFILES]

        # Enforce shape by action.
        if decision.action == "delegate" and len(decision.agents) != 1:
            decision.agents = decision.agents[:1]
        elif decision.action in ("multi_delegate", "clarify") and len(decision.agents) > 2:
            decision.agents = decision.agents[:2]
        elif decision.action in ("out_of_scope", "direct"):
            decision.agents = []

        log.info(
            "LLM router | action=%s | agents=%s | confidence=%.2f | reason=%r | query=%r",
            decision.action,
            decision.agents,
            decision.confidence,
            decision.reason[:200],
            query[:200],
        )
        return decision

    except Exception as exc:
        log.warning("LLM router error (falling back to clarify): %s", exc)
        return RouteDecision(
            action="clarify",
            agents=list(SPECIALIST_ROUTING_PROFILES.keys())[:2],
            confidence=0.0,
            reason=f"router_error:{type(exc).__name__}",
        )
