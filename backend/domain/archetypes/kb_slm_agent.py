"""
Ask HR SLM agent — RAG + leave-balance API, all powered by the internal SLM
(deepseek-r1:1.5b via Ollama).

Flow (option-3 two-stage):
  1. Classifier SLM call (non-streaming, hidden from user) tags the query
     as LEAVE_BALANCE | KB_QUERY | GREETING.
  2. Based on intent, fetch the appropriate context:
       - LEAVE_BALANCE → call SLT ERP leave-balance API
       - KB_QUERY      → hybrid retrieval from askhrslm_docs
       - GREETING      → no context
  3. Streaming SLM call answers using that context. <think> tokens are
     stripped at the chat-router layer.

Tool-calling is intentionally avoided — deepseek-r1:1.5b doesn't reliably
emit valid tool-call JSON, so we route deterministically.
"""

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, trim_messages
from langgraph.graph import END, START, StateGraph

from core.llm_slm import get_slm_chat_model
from domain.state import AgentState
from domain.tools.api_tools import fetch_leave_balance_for_user
from domain.tools.rag_tools_slm import search_hr_slm_kb

log = logging.getLogger(__name__)

llm = get_slm_chat_model()

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_VALID_INTENTS = {"LEAVE_BALANCE", "KB_QUERY", "GREETING"}

# Deterministic fast-path for the leave-balance intent. The 1.5B classifier
# is unreliable, and most personal leave questions follow predictable
# phrasing. Matching here skips the classifier call entirely (~8s saved).
_LEAVE_BALANCE_RE = re.compile(
    r"\b("
    r"my\s+leave|leave\s+balance|leaves?\s+(remaining|left|available)|"
    r"how\s+many\s+leaves?|"
    r"(annual|casual|sick|medical)\s+leave\s+(balance|remaining|left)|"
    r"days?\s+(of\s+)?leave\s+(remaining|left|available|do\s+i\s+have)"
    r")\b",
    re.IGNORECASE,
)

# Cheap greeting fast-path — short messages that are clearly social.
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hola|good\s+(morning|afternoon|evening)|"
    r"thanks?|thank\s+you|thx|ty|bye|goodbye|cheers)[\s!.?]*$",
    re.IGNORECASE,
)


def _last_user_text(messages) -> str:
    for msg in reversed(messages):
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "user":
            return str(msg[1])
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _classify_intent(query: str) -> str:
    """Tag the query as LEAVE_BALANCE / GREETING / KB_QUERY using regex.

    The 1.5B model proved unreliable at classification, so routing is now
    fully deterministic. KB_QUERY is the default fall-through.
    """
    if _LEAVE_BALANCE_RE.search(query):
        log.info(f"[SLM router] intent=LEAVE_BALANCE | query={query!r}")
        return "LEAVE_BALANCE"
    if _GREETING_RE.match(query):
        log.info(f"[SLM router] intent=GREETING | query={query!r}")
        return "GREETING"
    log.info(f"[SLM router] intent=KB_QUERY | query={query!r}")
    return "KB_QUERY"


async def call_model(state: AgentState) -> dict:
    user_query = _last_user_text(state["messages"])
    user_id = state.get("user_id", "") or ""

    intent = _classify_intent(user_query)

    # ── LEAVE_BALANCE: bypass the SLM entirely. ──────────────────────
    # The leave API already returns clean, structured text. Asking a 1.5B
    # model to "reformat" it just produces hallucinations — output the data
    # verbatim with a short header.
    if intent == "LEAVE_BALANCE":
        leave_data = fetch_leave_balance_for_user(user_id)
        answer = f"Here is your current leave balance from the SLT ERP:\n\n{leave_data}"
        return {"messages": [AIMessage(content=answer)]}

    # ── GREETING / KB_QUERY: SLM call with appropriate context. ──────
    if intent == "GREETING":
        context_block = ""
        rules = (
            "Respond with a short, warm greeting. Briefly mention you are the "
            "Ask HR assistant powered by SLTMobitel's internal SLM. Do not cite sources."
        )
    else:  # KB_QUERY
        context = await search_hr_slm_kb.ainvoke({"query": user_query})
        context_block = f"CONTEXT FROM HR KNOWLEDGE BASE:\n{context}"
        rules = (
            'If the context does not contain the answer, reply exactly: '
            '"I don\'t have that information in the HR knowledge base." '
            "Do NOT use general knowledge. Stay strictly within the provided context."
        )

    system_prompt = f"""You are the Ask HR assistant for SLTMobitel, powered by an internal company-hosted small language model.

{context_block}

RULES:
{rules}

FORMATTING:
1. Start with a direct one-sentence answer (BLUF), then optional bullets.
2. Bold key values (durations, dates, amounts) using **markdown**.
3. Be concise. No closing pleasantries.
4. For KB answers, end with a "Sources:" line listing unique [filename](link) pairs you actually used. Skip Sources for greetings.
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

    messages = [{"role": "system", "content": system_prompt}] + trimmed
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


def build_kb_slm_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_edge(START, "agent")
    workflow.add_edge("agent", END)
    return workflow
