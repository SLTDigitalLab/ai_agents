"""Ask SLT supervisor agent.

The supervisor is the default entry point. It does three things:
1. Answers general/platform/help questions directly.
2. Clarifies ambiguous specialist questions.
3. Delegates clear specialist questions to the configured specialist agents.

Specialist routing is done with vector similarity against maintained routing
profiles. General/help/platform questions are handled with lightweight rules.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, trim_messages
from langgraph.graph import END, START, StateGraph

from core.llm import get_chat_model, get_embedding_model
from domain.archetypes.kb_agent import build_kb_workflow
from domain.archetypes.kb_api_agent import build_kb_api_workflow
from domain.archetypes.kb_form_agent import build_kb_form_workflow
from domain.config.supervisor_routing import (
    CLARIFICATION_CHOICE_ALIASES,
    FOLLOW_UP_PATTERNS,
    FOLLOW_UP_STICKINESS_BOOST,
    GENERAL_HELP_PATTERNS,
    LOW_CONFIDENCE_THRESHOLD,
    MIN_ROUTE_MARGIN,
    OUT_OF_SCOPE_THRESHOLD,
    SHORT_FOLLOW_UP_MAX_WORDS,
    SPECIALIST_ROUTING_PROFILES,
    STRONG_ROUTE_THRESHOLD,
    VAGUE_SPECIALIST_PATTERNS,
)
from domain.state import AgentState

logger = logging.getLogger(__name__)
llm = get_chat_model()

SPECIALIST_BUILDERS = {
    "hr": build_kb_api_workflow,
    "finance": build_kb_workflow,
    "admin": build_kb_workflow,
    "it": build_kb_workflow,
    "cio": build_kb_workflow,
}


def _extract_text(content: Any) -> str:
    """Normalize LangChain message content into plain text."""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(str(block["text"]))
        return " ".join(part.strip() for part in text_parts if part).strip()

    return str(content).strip()


def _latest_user_query(state: AgentState) -> str:
    """Return the newest human message from state."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "human":
            return _extract_text(message.content)
    return ""


def _profile_text(agent_id: str) -> str:
    """Build the routing profile text used for embeddings."""
    profile = SPECIALIST_ROUTING_PROFILES[agent_id]
    description = profile["description"]
    keywords = ", ".join(profile["keywords"])
    examples = " | ".join(profile["examples"])
    return (
        f"Agent: {profile['display_name']}\n"
        f"Domain: {description}\n"
        f"Keywords: {keywords}\n"
        f"Examples: {examples}"
    )


@lru_cache(maxsize=1)
def _specialist_profile_vectors() -> dict[str, list[float]]:
    """Embed all specialist routing profiles once and cache them."""
    embedding_model = get_embedding_model()
    agent_ids = list(SPECIALIST_ROUTING_PROFILES.keys())
    profile_texts = [_profile_text(agent_id) for agent_id in agent_ids]
    vectors = embedding_model.embed_documents(profile_texts)
    return dict(zip(agent_ids, vectors))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two dense vectors."""
    numerator = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return numerator / (norm_a * norm_b)


def _is_general_help_question(query: str) -> bool:
    """Detect supervisor-native platform/help/navigation queries."""
    query = query.strip().lower()
    return any(re.search(pattern, query) for pattern in GENERAL_HELP_PATTERNS)

def _is_bare_greeting(query: str) -> bool:
    """Detect greeting-only turns that should ignore prior thread context."""
    stripped = query.strip().lower()
    return bool(
        re.fullmatch(
            r"(hi|hello|hey|good morning|good afternoon|good evening)[!. ]*",
            stripped,
        )
    )

def _is_vague_specialist_prompt(query: str) -> bool:
    """Detect low-information prompts that should be clarified before routing."""
    stripped = query.strip().lower()

    if any(re.search(pattern, stripped) for pattern in VAGUE_SPECIALIST_PATTERNS):
        return True

    tokens = re.findall(r"\w+", stripped)
    low_signal_words = {"help", "something", "issue", "problem", "support", "question"}

    return (
        len(tokens) <= 4
        and any(token in low_signal_words for token in tokens)
        and not _is_general_help_question(stripped)
    )


def _is_short_follow_up(query: str) -> bool:
    """Detect short follow-up turns that should inherit route bias."""
    stripped = query.strip().lower()
    word_count = len(re.findall(r"\w+", stripped))
    if word_count <= SHORT_FOLLOW_UP_MAX_WORDS and any(
        re.search(pattern, stripped) for pattern in FOLLOW_UP_PATTERNS
    ):
        return True
    return False


def _clarification_display_names(agent_ids: list[str]) -> list[str]:
    """Return display names for a list of specialist ids."""
    return [
        str(SPECIALIST_ROUTING_PROFILES[agent_id]["display_name"])
        for agent_id in agent_ids
        if agent_id in SPECIALIST_ROUTING_PROFILES
    ]


def _matches_clarification_choice(query: str, agent_id: str) -> bool:
    """Check whether a clarification reply maps to a given specialist."""
    normalized = query.strip().lower()
    aliases = CLARIFICATION_CHOICE_ALIASES.get(agent_id, ())
    candidate_terms = (agent_id, *aliases)

    for term in candidate_terms:
        escaped = re.escape(term.lower())
        if re.fullmatch(rf"{escaped}", normalized):
            return True
        if re.fullmatch(
            rf"(it is|it's|its|for|about|this is|this is about|go with|choose|select|pick)?\s*{escaped}(\s+please)?",
            normalized,
        ):
            return True

    return False


def _resolve_clarification_choice(query: str, options: list[str]) -> str | None:
    """Resolve a user clarification reply to one specialist id."""
    matches = [agent_id for agent_id in options if _matches_clarification_choice(query, agent_id)]
    if len(matches) == 1:
        return matches[0]
    return None


def _replace_latest_human_message(messages: list[Any], new_query: str) -> list[Any]:
    """Replace the newest human message so the specialist receives the real query."""
    updated_messages = list(messages)

    for index in range(len(updated_messages) - 1, -1, -1):
        message = updated_messages[index]

        if getattr(message, "type", None) == "human":
            updated_messages[index] = HumanMessage(content=new_query)
            return updated_messages

        if isinstance(message, tuple) and len(message) == 2 and message[0] in {"user", "human"}:
            updated_messages[index] = HumanMessage(content=new_query)
            return updated_messages

    updated_messages.append(HumanMessage(content=new_query))
    return updated_messages


def _clarification_options_from_scores(
    scored: list[tuple[str, float]],
    score_gap: float,
) -> list[str]:
    """Pick the specialist choices that should be shown to the user."""
    top_agent, top_score = scored[0]
    second_agent, second_score = scored[1]

    if score_gap < MIN_ROUTE_MARGIN:
        return [top_agent, second_agent]

    if top_score < LOW_CONFIDENCE_THRESHOLD and second_score >= OUT_OF_SCOPE_THRESHOLD:
        return [top_agent, second_agent]

    return [top_agent]


async def _score_specialists(
    query: str,
    last_specialist_agent: str | None,
) -> list[tuple[str, float]]:
    """Embed the query and score it against specialist routing profiles."""
    embedding_model = get_embedding_model()
    query_vector = await asyncio.to_thread(embedding_model.embed_query, query)
    profile_vectors = _specialist_profile_vectors()

    scored: list[tuple[str, float]] = []
    for agent_id, vector in profile_vectors.items():
        score = _cosine_similarity(query_vector, vector)
        if last_specialist_agent == agent_id and _is_short_follow_up(query):
            score += FOLLOW_UP_STICKINESS_BOOST
        scored.append((agent_id, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


async def route_request(state: AgentState) -> dict:
    """Choose whether to answer directly, delegate, clarify, or reject as out-of-scope."""
    query = _latest_user_query(state)
    last_specialist_agent = state.get("last_specialist_agent")
    pending_clarification = bool(state.get("pending_clarification"))
    clarification_options = list(state.get("clarification_options") or [])
    original_query = state.get("original_query") or ""

    if not query:
        return {
            "routing_action": "clarify",
            "routing_reason": "empty_query",
            "pending_clarification": True,
            "clarification_options": list(SPECIALIST_BUILDERS.keys()),
            "original_query": "",
        }

    # First handle a pending clarification turn.
    if pending_clarification and clarification_options:
        chosen_agent = _resolve_clarification_choice(query, clarification_options)
        if chosen_agent:
            logger.info(
                "Supervisor route | action=delegate | reason=clarification_choice | target=%s | original_query=%r | choice=%r",
                chosen_agent,
                original_query[:200],
                query[:200],
            )
            return {
                "routing_action": "delegate",
                "routed_agent_id": chosen_agent,
                "routing_reason": f"clarification_choice:{chosen_agent}",
                "routing_scores": {},
                "delegation_query": original_query or query,
                "pending_clarification": False,
                "clarification_options": [],
                "original_query": "",
            }

    if _is_general_help_question(query):
        logger.info(
            "Supervisor route | action=direct | reason=general_help_rule | query=%r",
            query[:200],
        )
        return {
            "routing_action": "direct",
            "routing_reason": "general_help_rule",
            "pending_clarification": False,
            "clarification_options": [],
            "original_query": "",
        }

    if _is_vague_specialist_prompt(query):
        logger.info(
            "Supervisor route | action=clarify | reason=vague_prompt | query=%r",
            query[:200],
        )
        return {
            "routing_action": "clarify",
            "routing_reason": "vague_prompt",
            "routing_scores": {},
            "pending_clarification": True,
            "clarification_options": list(SPECIALIST_BUILDERS.keys()),
            "original_query": query,
        }

    scored = await _score_specialists(query, last_specialist_agent)
    top_agent, top_score = scored[0]
    second_agent, second_score = scored[1]
    score_gap = top_score - second_score
    rounded_scores = {agent_id: round(score, 4) for agent_id, score in scored}

    if top_score >= STRONG_ROUTE_THRESHOLD and score_gap >= MIN_ROUTE_MARGIN:
        logger.info(
            "Supervisor route | action=delegate | target=%s | top=%.4f | second=%s %.4f | last=%s | query=%r",
            top_agent,
            top_score,
            second_agent,
            second_score,
            last_specialist_agent,
            query[:200],
        )
        return {
            "routing_action": "delegate",
            "routed_agent_id": top_agent,
            "routing_reason": f"vector_match:{top_agent}",
            "routing_scores": rounded_scores,
            "delegation_query": query,
            "pending_clarification": False,
            "clarification_options": [],
            "original_query": "",
        }

    if top_score < OUT_OF_SCOPE_THRESHOLD:
        logger.info(
            "Supervisor route | action=out_of_scope | top=%s %.4f | second=%s %.4f | query=%r",
            top_agent,
            top_score,
            second_agent,
            second_score,
            query[:200],
        )
        return {
            "routing_action": "out_of_scope",
            "routing_reason": "very_low_specialist_similarity",
            "routing_scores": rounded_scores,
            "pending_clarification": False,
            "clarification_options": [],
            "original_query": "",
        }

    clarification_targets = _clarification_options_from_scores(scored, score_gap)

    logger.info(
        "Supervisor route | action=clarify | top=%s %.4f | second=%s %.4f | last=%s | options=%s | query=%r",
        top_agent,
        top_score,
        second_agent,
        second_score,
        last_specialist_agent,
        clarification_targets,
        query[:200],
    )
    return {
        "routing_action": "clarify",
        "routed_agent_id": top_agent,
        "routing_reason": "low_confidence_or_small_margin",
        "routing_scores": rounded_scores,
        "pending_clarification": True,
        "clarification_options": clarification_targets,
        "original_query": query,
    }


async def answer_directly(state: AgentState) -> dict:
    """Answer general help, platform, and navigation questions directly."""
    query = _latest_user_query(state)

    # Greeting-only turns should not inherit old specialist context.
    if _is_bare_greeting(query):
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Hi! I’m Workmate AI. I can help with platform questions "
                        "or requests related to **HR**, **Finance**, **IT**, **CIO**, or **Admin**."
                    )
                )
            ],
            "last_specialist_agent": None,
        }

    system_prompt = """You are Workmate AI, the main supervisor assistant for an internal multi-agent platform.

You answer ONLY these categories directly:
- platform help and navigation
- greetings, thanks, brief conversational turns
- questions about what Workmate AI can do
- questions about which specialist should handle a topic
- general workplace help that does not require specialist policy retrieval

Available specialists:
- HR: leave, benefits, recruitment, employee policy, staff support
- Finance: salary, payroll, budgets, invoices, expense claims, payments
- IT: technical support, hardware, software, network, access management
- CIO: digital transformation, IT strategy, enterprise architecture, technology roadmap
- Admin: facilities, transport, security, parking, office support

Rules:
1. Be concise, clear, and practical.
2. If the user asks which specialist should handle something, answer directly.
3. Do not invent HR, finance, IT, CIO, or admin facts.
4. If the user is clearly asking a specialist-domain factual question, say that you can route them to the right specialist and name the best fit.
5. Do not mention routing scores, thresholds, embeddings, vectors, or internal implementation.
6. Do not end with a closing question.
"""

    trimmed = trim_messages(
        state["messages"],
        max_tokens=8,
        strategy="last",
        token_counter=len,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )

    response = await llm.ainvoke(
        [{"role": "system", "content": system_prompt}] + trimmed
    )
    return {"messages": [response]}


async def ask_for_clarification(state: AgentState) -> dict:
    """Ask the user to clarify ambiguous specialist intent."""
    options = list(state.get("clarification_options") or [])
    reason = state.get("routing_reason")
    display_names = _clarification_display_names(options)

    if reason == "vague_prompt" or not display_names:
        content = (
            "Please tell me which area this is about: **HR**, **Finance**, **IT**, **CIO**, or **Admin**."
        )
        return {"messages": [AIMessage(content=content)]}

    if reason == "low_confidence_or_small_margin" and len(display_names) == 2:
        content = (
            f"I want to route this correctly, but it could belong to **{display_names[0]}** "
            f"or **{display_names[1]}**. Please reply with one of those."
        )
        return {"messages": [AIMessage(content=content)]}

    if len(display_names) == 1:
        content = (
            f"I think this may belong to **{display_names[0]}**. "
            f"Please reply with **{display_names[0]}** if that is correct, or say **HR**, **Finance**, **IT**, **CIO**, or **Admin**."
        )
        return {"messages": [AIMessage(content=content)]}

    choices = ", ".join(f"**{name}**" for name in display_names[:-1])
    content = (
        f"Please reply with one of these areas: {choices}, or **{display_names[-1]}**."
    )
    return {"messages": [AIMessage(content=content)]}


async def respond_out_of_scope(state: AgentState) -> dict:
    """Respond when the query is outside the supported domains."""
    content = (
        "I cannot answer that request. "
        "I am limited to platform/help questions and requests related to "
        "**HR**, **Finance**, **IT**, **CIO**, and **Admin**."
    )
    return {"messages": [AIMessage(content=content)]}


def _build_delegate_node(agent_id: str):
    """Create a supervisor node that delegates to one specialist graph."""
    specialist_graph = SPECIALIST_BUILDERS[agent_id]().compile()

    async def _delegate_to_specialist(state: AgentState) -> dict:
        specialist_state = dict(state)
        specialist_state["agent_id"] = agent_id

        delegation_query = state.get("delegation_query")
        if delegation_query:
            specialist_state["messages"] = _replace_latest_human_message(
                state.get("messages", []),
                delegation_query,
            )

        try:
            result = await specialist_graph.ainvoke(specialist_state)
            ai_messages = [
                message
                for message in result.get("messages", [])
                if getattr(message, "type", None) == "ai"
            ]
            final_message = ai_messages[-1] if ai_messages else AIMessage(
                content="I could not get a response from the specialist agent."
            )
            return {
                "messages": [final_message],
                "last_specialist_agent": agent_id,
                "pending_clarification": False,
                "clarification_options": [],
                "original_query": "",
                "delegation_query": "",
            }
        except Exception:
            logger.exception("Supervisor delegation failed for agent=%s", agent_id)
            logger.info("Delegated final message raw content: %r", final_message.content)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"I ran into an internal error while routing this to **{SPECIALIST_ROUTING_PROFILES[agent_id]['display_name']}**. "
                            f"Please try again."
                        )
                    )
                ]
            }

    return _delegate_to_specialist


def _route_to_node(state: AgentState) -> str:
    """Translate routing state into the next LangGraph node name."""
    action = state.get("routing_action")

    if action == "direct":
        return "answer_directly"
    if action == "clarify":
        return "ask_for_clarification"
    if action == "out_of_scope":
        return "respond_out_of_scope"
    if action == "delegate":
        routed_agent_id = state.get("routed_agent_id")
        if routed_agent_id in SPECIALIST_BUILDERS:
            return f"delegate_{routed_agent_id}"
    return "ask_for_clarification"


def build_supervisor_workflow() -> StateGraph:
    """Build the Ask SLT supervisor workflow."""
    workflow = StateGraph(AgentState)

    workflow.add_node("route_request", route_request)
    workflow.add_node("answer_directly", answer_directly)
    workflow.add_node("ask_for_clarification", ask_for_clarification)
    workflow.add_node("respond_out_of_scope", respond_out_of_scope)

    for agent_id in SPECIALIST_BUILDERS:
        workflow.add_node(f"delegate_{agent_id}", _build_delegate_node(agent_id))

    workflow.add_edge(START, "route_request")
    workflow.add_conditional_edges(
        "route_request",
        _route_to_node,
        {
            "answer_directly": "answer_directly",
            "ask_for_clarification": "ask_for_clarification",
            "respond_out_of_scope": "respond_out_of_scope",
            "delegate_hr": "delegate_hr",
            "delegate_finance": "delegate_finance",
            "delegate_admin": "delegate_admin",
            "delegate_it": "delegate_it",
            "delegate_cio": "delegate_cio",
        },
    )

    workflow.add_edge("answer_directly", END)
    workflow.add_edge("ask_for_clarification", END)
    workflow.add_edge("respond_out_of_scope", END)
    workflow.add_edge("delegate_hr", END)
    workflow.add_edge("delegate_finance", END)
    workflow.add_edge("delegate_admin", END)
    workflow.add_edge("delegate_it", END)
    workflow.add_edge("delegate_cio", END)

    return workflow