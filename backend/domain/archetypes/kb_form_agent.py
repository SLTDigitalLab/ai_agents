"""
Archetype 3 - Knowledge-Base + Form (Generative UI) agent graph.

Used by: Ask Lifestore, Ask Enterprise.

The LLM answers product/service questions via RAG and triggers a frontend
form when the user wants to buy, order, or subscribe. No backend state
machine is needed — the React frontend handles the form rendering when
it detects the ``[RENDER_*_FORM]`` token in the response.

Flow:
    START ──► agent (LLM) ──► tools_condition ──► tools (RAG) ──► agent ──► END
"""

from langchain_core.messages import trim_messages
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from core.llm import get_chat_model
from domain.state import AgentState
from domain.tools.rag_tools import search_knowledge_base

# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()

# Bind the RAG tool
tools = [search_knowledge_base]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
async def call_model(state: AgentState) -> dict:
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

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST (BLUF): Always start your response with a direct, one-sentence answer to the user's specific question. Do not use filler phrases like "According to the policy..." or "Here are the guidelines...".
2. STRICTLY RELEVANT: Only answer exactly what the user asked. Do not add extra related policy details unless explicitly requested.
3. CONCISENESS: Prefer concise answers to improve response time and user experience. Use standard Markdown bullet points (`*` or `-`), starting each point on a NEW line.
4. BOLD KEY METRICS: Always bold crucial variables like times, durations, prices (e.g., **Rs. 1,500**), and quantities to make the text highly scannable.
5. MARKDOWN SPACING: Use a double newline (blank line) between the direct answer and the bulleted list to ensure proper rendering. Do NOT use non-standard bullet characters like `•`.
6. NO CLOSING QUESTIONS: Do not end your response with phrases like "Is there anything else I can help you with?". Just stop once the answer is complete.

CITATIONS:
1. You may see `[Source: ... | Link: ...]` tags in the retrieved context. 
2. You MUST IGNORE these tags.
3. DO NOT include any "Sources:" section or links in your response.

Example Purchase Response: "I can certainly help you order a Peo TV connection! Please fill out the secure request form below to get started. {form_token}"
"""

    # ── Sentiment-aware tone adjustment ──────────────────────────────
    sentiment = state.get("sentiment", "neutral")
    if sentiment in ("frustrated", "angry"):
        system_prompt += f"""

TONE ADJUSTMENT:
The user appears to be {sentiment}. Be extra empathetic, patient, and acknowledge their frustration before answering. Use a warm, understanding tone."""

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

    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


# ── Build the (uncompiled) workflow ──────────────────────────────────────
def build_kb_form_workflow() -> StateGraph:
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
