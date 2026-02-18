"""
Archetype 3 – Knowledge-Base + Form (Generative UI) agent graph.

Used by: Ask Lifestore, Ask Enterprise.

The LLM answers product/service questions via RAG and triggers a frontend
form when the user wants to buy, order, or subscribe. No backend state
machine is needed — the React frontend handles the form rendering when
it detects the ``[RENDER_*_FORM]`` token in the response.

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
    model="gemini-2.5-flash",
    google_api_key=settings.GOOGLE_API_KEY,
)

# Bind the RAG tool
tools = [search_knowledge_base]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
def call_model(state: AgentState) -> dict:
    """Invoke the LLM with a Generative-UI-aware system prompt."""
    agent_id = state["agent_id"]

    # Determine the correct form token based on the agent
    form_token = (
        "[RENDER_LIFESTORE_FORM]"
        if "lifestore" in agent_id.lower()
        else "[RENDER_ENTERPRISE_FORM]"
    )

    system_prompt = f"""You are the Ask {agent_id.upper()} AI assistant for SLTMobitel.

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask {agent_id.upper()} assistant. How can I help you today?"
- If the user asks about a completely unrelated department, decline politely and suggest they ask the appropriate Ask SLT agent.

STRICT RULES FOR FACTUAL QUESTIONS:
1. You MUST use `search_knowledge_base` to answer general questions about products, services, or pricing.
2. DO NOT use your pre-trained general knowledge to answer factual or product questions.
3. If the user expresses an intent to BUY, PURCHASE, ORDER, or SUBSCRIBE to a product/service, you MUST politely agree to help and append a specific UI trigger token to the very end of your response.
   - Append: {form_token}
4. Do NOT ask the user for their name, NIC, or details in the chat. The form will handle that.
5. CRITICAL: When the context contains multiple items, you MUST carefully isolate the specific item the user asked about. DO NOT mix up details belonging to one product with another.

Example Purchase Response: "I can certainly help you order a Peo TV connection! Please fill out the secure request form below to get started. {form_token}"
"""

    # Trim to the last 5 messages + system prompt for the LLM window,
    # but the full history stays in state for the checkpointer to persist.
    trimmed = trim_messages(
        state["messages"],
        max_tokens=5,
        strategy="last",
        token_counter=len,
        include_system=True,
        allow_partial=False,
    )

    # Prepend the system prompt to the trimmed messages
    messages = [{"role": "system", "content": system_prompt}] + trimmed

    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


# ── Build the (uncompiled) workflow ──────────────────────────────────────
def build_kb_form_workflow() -> StateGraph:
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
