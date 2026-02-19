"""
Archetype 1 – Knowledge-Base-only agent graph.

Used by: Ask Finance, Ask Admin, Ask Process.

Flow:
    START ──► agent (LLM) ──► tools_condition ──► tools (RAG) ──► agent ──► END
"""

from langchain_core.messages import trim_messages
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from domain.state import AgentState
from domain.tools.rag_tools import search_knowledge_base

# ── LLM setup ────────────────────────────────────────────────────────────
llm = ChatGoogleGenerativeAI(
    model="gemini-3-flash-preview",
    google_api_key=settings.GOOGLE_API_KEY,
)

# Bind the RAG tool so the LLM can decide to call it
tools = [search_knowledge_base]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
def call_model(state: AgentState) -> dict:
    """Invoke the LLM with a system prompt tailored to the active agent."""
    agent_id = state["agent_id"]

    system_prompt = f"""You are the Ask {agent_id.upper()} AI assistant for SLTMobitel.
Your primary purpose is to answer questions related to your specific department ({agent_id}).

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask {agent_id.upper()} assistant. How can I help you today?"
- If the user asks about a different department (e.g., HR, Finance) or a completely unrelated topic, decline politely and suggest they ask the appropriate Ask SLT agent.

STRICT RULES FOR FACTUAL QUESTIONS:
1. You MUST ALWAYS use the `search_knowledge_base` tool to find factual information before answering.
2. You MUST ONLY answer based on the context returned by the tool.
3. DO NOT use your pre-trained general knowledge to answer factual or policy questions.
4. If the tool returns an empty result, or if the retrieved context does not clearly contain the answer, you MUST decline to answer.
5. CRITICAL: When the context contains multiple items (like different types of loans, leaves, or policies), you MUST carefully isolate the specific item the user asked about.
6. DO NOT mix up numbers, durations, or rules belonging to one item with another.
7. Before outputting the final answer, silently verify that the attribute you are providing belongs EXCLUSIVELY to the requested entity in the source text.
"""

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
def build_kb_workflow() -> StateGraph:
    """Return an uncompiled StateGraph – registry.py will compile it
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
