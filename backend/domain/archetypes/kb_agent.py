"""
Archetype 1 - Knowledge-Base-only agent graph.

Used by: Ask Finance, Ask Admin, Ask Process.

Flow:
    START ──► agent (LLM) ──► tools_condition ──► tools (RAG) ──► agent ──► END
"""

from langchain_core.messages import AIMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.llm import get_chat_model
from domain.state import AgentState
from domain.tools.rag_tools import search_knowledge_base

# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()

# Bind the RAG tool so the LLM can decide to call it
tools = [search_knowledge_base]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    """Invoke the LLM with a system prompt tailored to the active agent."""
    cached = (config.get("configurable") or {}).get("cached_response")
    if cached:
        return {"messages": [AIMessage(content=cached)]}

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

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST (BLUF): Always start your response with a direct, one-sentence answer to the user's specific question. Do not use filler phrases like "According to the policy..." or "Here are the guidelines...".
2. STRICTLY RELEVANT: Only extract and provide the rules that directly answer the user's immediate question. Do not include adjacent rules, exceptions, or background context unless explicitly asked.
3. EXTREME CONCISENESS: Strip out all conversational fluff. Present the required rules using standard Markdown bullet points (`*` or `-`), starting each point on a NEW line.
4. BOLD KEY METRICS: Always bold crucial variables like times (e.g., **8.30 a.m.**), durations (e.g., **3.5 hours**), and quantities to make the text highly scannable.
5. MARKDOWN SPACING: Use a double newline (blank line) between the direct answer and the bulleted list to ensure proper rendering. Do NOT use non-standard bullet characters like `•`.
6. NO CLOSING QUESTIONS: Do not end your response with phrases like "Is there anything else I can help you with?". Just stop once the answer is complete.

CITATIONS:
1. In the context returned by the tool, each chunk starts with `[Source: <filename> | Link: <url>]`.
2. You MUST keep track of which source(s) and link(s) you used to generate your answer.
3. At the very end of your response, after a double newline, add a "Sources:" section.
4. List the unique sources you actually used as Markdown links: `[Filename](URL)`, separated by commas.
   Example: "Sources: [policy_2024.pdf](http://lnk.to/1), [guidelines.docx](http://lnk.to/2)"
5. If no documents were used (e.g., for a greeting), do not add the Sources section.
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
def build_kb_workflow() -> StateGraph:
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
