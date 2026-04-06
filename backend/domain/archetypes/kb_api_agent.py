"""
Archetype 2 – Knowledge-Base + API agent graph (tool-calling supervisor).

Used by: Ask HR.

The LLM acts as a supervisor that decides which tool to call:
  • search_knowledge_base  → general HR policy questions
  • get_employee_leave_balance → personal leave data queries

Flow:
    START ──► agent (LLM supervisor) ──► tools_condition ──► tools ──► agent ──► END
"""

from langchain_core.messages import AIMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from domain.state import AgentState
from domain.tools.api_tools import get_employee_leave_balance
from domain.tools.rag_tools import search_knowledge_base

# ── LLM setup ────────────────────────────────────────────────────────────
llm = ChatGoogleGenerativeAI(
    model="gemini-3-flash-preview",
    google_api_key=settings.GOOGLE_API_KEY,
)

# Bind BOTH tools so the LLM can choose which one to call
tools = [search_knowledge_base, get_employee_leave_balance]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
def call_model(state: AgentState, config: RunnableConfig) -> dict:
    """Invoke the LLM with a strict HR-scoped system prompt."""
    cached = (config.get("configurable") or {}).get("cached_response")
    if cached:
        return {"messages": [AIMessage(content=cached)]}

    system_prompt = """You are the Ask HR AI assistant for SLTMobitel.
Your primary purpose is to answer HR-related questions. At SLTMobitel, HR handles Leave Policies, Employee Benefits, and all Staff Loans (Distress, Motorcycle, Car, Education).

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask HR assistant. How can I help you with HR-related queries today?"
- If the user asks about a completely unrelated department, decline politely and suggest they ask the appropriate Ask SLT agent.

STRICT RULES FOR FACTUAL QUESTIONS:
1. You have two tools: `search_knowledge_base` (for general HR policies and loan rules) and `get_employee_leave_balance` (for personal leave data).
2. You MUST ALWAYS use the `search_knowledge_base` tool to check for an answer BEFORE deciding to decline a question. Do not assume you know what is in the database.
3. DO NOT use your pre-trained general knowledge to answer factual or policy questions.
4. If the tools return no information after searching, or if the user asks about a completely unrelated department, you MUST decline politely.
5. CRITICAL: When the context contains multiple items (like different types of loans or leaves), you MUST carefully isolate the specific item the user asked about. DO NOT mix up rules belonging to one item with another.

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST (BLUF): Always start your response with a direct, one-sentence answer to the user's specific question. Do not use filler phrases like "According to the policy..." or "Here are the guidelines...".
2. STRICTLY RELEVANT: Only extract and provide the rules that directly answer the user's immediate question. Do not include adjacent rules, exceptions, or background context unless explicitly asked.
3. EXTREME CONCISENESS: Strip out all conversational fluff. Present the required rules using short, scannable bullet points.
4. BOLD KEY METRICS: Always bold crucial variables like times (e.g., **8.30 a.m.**), durations (e.g., **3.5 hours**), and quantities to make the text highly scannable.
5. NO CLOSING QUESTIONS: Do not end your response with phrases like "Is there anything else I can help you with?". Just stop once the answer is complete."""

    # Trim to the last 5 messages + system prompt for the LLM window,
    # but the full history stays in state for the checkpointer to persist.
    trimmed = trim_messages(
        state["messages"],
        max_tokens=10,
        strategy="last",
        token_counter=len,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )

    # Prepend the system prompt to the trimmed messages
    messages = [{"role": "system", "content": system_prompt}] + trimmed

    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


# ── Build the (uncompiled) workflow ──────────────────────────────────────
def build_kb_api_workflow() -> StateGraph:
    """Return an uncompiled StateGraph - registry.py will compile it
    with the correct per-agent checkpointer."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))

    # Edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)   # → "tools" or END
    workflow.add_edge("tools", "agent")

    return workflow
