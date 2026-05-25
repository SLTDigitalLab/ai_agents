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

# Standard classification regexes
_LEAVE_BALANCE_RE = re.compile(
    r"\b("
    r"my\s+leave|leave\s+balance|leaves?\s+(remaining|left|available)|"
    r"how\s+many\s+leaves?|"
    r"(annual|casual|sick|medical)\s+leave\s+(balance|remaining|left)|"
    r"days?\s+(of\s+)?leave\s+(remaining|left|available|do\s+i\s+have)"
    r")\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hola|good\s+(morning|afternoon|evening)|"
    r"thanks?|thank\s+you|thx|ty|bye|goodbye|cheers)[\s!.?]*$",
    re.IGNORECASE,
)

# HITL Yes/No Regexes
_YES_RE = re.compile(r"^\s*(yes|y|yeah|yep|sure|please)\b", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|n|nope|nah)\b", re.IGNORECASE)

# The exact prompt the bot uses to ask for clarification.
# The frontend can use this string to trigger UI buttons if needed.
_LEAVE_CLARIFICATION_MSG = "Do you want a detailed explanation of your leave balance? (Please choose Yes or No)"


def _last_user_text(messages) -> str:
    for msg in reversed(messages):
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "user":
            return str(msg[1])
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _last_ai_text(messages) -> str:
    """Helper to check what the AI asked in the previous turn."""
    for msg in reversed(messages):
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "ai":
            return str(msg[1])
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _classify_intent(query: str, messages: list) -> str:
    """Tag the query. Checks conversation history first to see if we are awaiting a Yes/No."""
    last_ai_msg = _last_ai_text(messages)
    
    # Check if the user is responding to our Yes/No prompt
    if _LEAVE_CLARIFICATION_MSG in last_ai_msg:
        if _YES_RE.search(query):
            log.info(f"[SLM router] intent=LEAVE_YES | query={query!r}")
            return "LEAVE_YES"
        if _NO_RE.search(query):
            log.info(f"[SLM router] intent=LEAVE_NO | query={query!r}")
            return "LEAVE_NO"
        # If they reply with something unrelated, fall through to normal routing

    # Standard initial classification
    if _LEAVE_BALANCE_RE.search(query):
        log.info(f"[SLM router] intent=LEAVE_BALANCE_ASK | query={query!r}")
        return "LEAVE_BALANCE_ASK"
    if _GREETING_RE.match(query):
        log.info(f"[SLM router] intent=GREETING | query={query!r}")
        return "GREETING"
        
    log.info(f"[SLM router] intent=KB_QUERY | query={query!r}")
    return "KB_QUERY"


async def call_model(state: AgentState) -> dict:
    user_query = _last_user_text(state["messages"])
    user_id = state.get("user_id", "") or ""

    intent = _classify_intent(user_query, state["messages"])

    # LEAVE BALANCE HITL FLOW
    
    # User asks for balance -> pause and prompt for preference
    if intent == "LEAVE_BALANCE_ASK":
        return {"messages": [AIMessage(content=_LEAVE_CLARIFICATION_MSG)]}

    # User chose "No" -> Output raw data fast-path
    elif intent == "LEAVE_NO":
        leave_data = fetch_leave_balance_for_user(user_id)
        answer = f"Here is your current leave balance from the SLT ERP:\n\n{leave_data}"
        return {"messages": [AIMessage(content=answer)]}

    # User chose "Yes" -> Use the SLM to generate a clear explanation
    elif intent == "LEAVE_YES":
        leave_data = fetch_leave_balance_for_user(user_id)
        context_block = f"RAW ERP LEAVE DATA:\n{leave_data}"
        rules = (
            "Explain the user's leave balance in a friendly, easy-to-read way. "
            "Break down the different categories (e.g., Annual, Sick, Casual). "
            "Do NOT invent or calculate new numbers. Only use the data provided."
        )
        system_prompt = _build_system_prompt(context_block, rules)
        return await _invoke_slm(state, system_prompt)

    # STANDARD FLOW (GREETING & KB) 
    
    elif intent == "GREETING":
        context_block = ""
        rules = (
            "Respond with a short, warm greeting. Briefly mention you are the "
            "Ask HR assistant powered by SLTMobitel's internal SLM. Do not cite sources."
        )
        system_prompt = _build_system_prompt(context_block, rules)
        return await _invoke_slm(state, system_prompt)

    else:  # KB_QUERY
        context = await search_hr_slm_kb.ainvoke({"query": user_query})
        context_block = f"CONTEXT FROM HR KNOWLEDGE BASE:\n{context}"
        rules = (
            'If the context does not contain the answer, reply exactly: '
            '"I don\'t have that information in the HR knowledge base." '
            "Do NOT use general knowledge. Stay strictly within the provided context."
        )
        system_prompt = _build_system_prompt(context_block, rules, include_sources=True)
        return await _invoke_slm(state, system_prompt)


def _build_system_prompt(context_block: str, rules: str, include_sources: bool = False) -> str:
    """Helper function to keep the system prompt generation clean."""
    sources_rule = '4. End with a "Sources:" line listing unique [filename](link) pairs you actually used.' if include_sources else '4. Skip Sources.'
    
    return f"""You are the Ask HR assistant for SLTMobitel, powered by an internal company-hosted small language model.

{context_block}

RULES:
{rules}

FORMATTING:
1. Start with a direct one-sentence answer (BLUF), then optional bullets.
2. Bold key values (durations, dates, amounts) using **markdown**.
3. Be concise. No closing pleasantries.
{sources_rule}
"""


async def _invoke_slm(state: AgentState, system_prompt: str) -> dict:
    """Helper function to execute the SLM call with trimmed message history."""
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