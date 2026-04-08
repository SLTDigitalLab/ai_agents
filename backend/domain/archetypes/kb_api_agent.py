"""
Archetype 2 - Knowledge-Base + API agent graph (tool-calling supervisor).

Used by: Ask HR.

The LLM acts as a supervisor that decides which tool to call:
  • search_knowledge_base  → general HR policy questions
  • get_employee_leave_balance → personal leave data queries

Flow:
    START ──► agent (LLM supervisor) ──► tools_condition ──► tools ──► agent ──► END
"""

from langchain_core.messages import trim_messages
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from core.llm import get_chat_model
from domain.state import AgentState
from domain.tools.api_tools import get_employee_leave_balance
from domain.tools.rag_tools import search_knowledge_base

# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()

# Bind BOTH tools so the LLM can choose which one to call
tools = [search_knowledge_base, get_employee_leave_balance]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
async def call_model(state: AgentState) -> dict:
    """Invoke the LLM with a strict HR-scoped system prompt."""
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
5. CRITICAL: When the context contains multiple items (like different types of loans or leaves), you MUST carefully isolate the specific item the user asked about. DO NOT mix up rules belonging to one item with another. Pay close attention to section headers like "[Section: ...]" in the retrieved context — they indicate which parent topic each chunk belongs to. Only use information from the section that matches the user's question. For example, if the user asks about Distress Loan, IGNORE any information from Motor Car Loan, Motorcycle Loan, or TDC Education Loan sections, even if those chunks appear in the results.

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST (BLUF): Always start your response with a direct, one-sentence answer to the user's specific question. Do not use filler phrases like "According to the policy..." or "Here are the guidelines...".
2. COMPREHENSIVE & HELPFUL: After the direct answer, include all closely related details from the retrieved context that help the user fully understand the topic — such as eligibility criteria, conditions, limits, top-up rules, and any practical examples found in the source. Do NOT omit useful details just to be brief. The goal is for the user to get a complete, self-contained answer without needing follow-up questions.
3. EXAMPLES: Whenever the source context contains numerical examples or calculations, ALWAYS include them in your answer. If the source does not contain an example but the answer involves a formula or calculation, create a simple illustrative example to help the user understand.
4. CLEAR STRUCTURE: Present supporting details using standard Markdown bullet points (`*` or `-`), starting each point on a NEW line. Keep the language clear and free of unnecessary filler, but do NOT sacrifice completeness for brevity.
5. BOLD KEY METRICS: Always bold crucial variables like times (e.g., **8.30 a.m.**), durations (e.g., **3.5 hours**), amounts, and quantities to make the text highly scannable.
6. MARKDOWN SPACING: Use a double newline (blank line) between the direct answer and the bulleted list to ensure proper rendering. Do NOT use non-standard bullet characters like `•`.
7. NO CLOSING QUESTIONS: Do not end your response with phrases like "Is there anything else I can help you with?". Just stop once the answer is complete.

CITATIONS:
1. In the context returned by the tool, each chunk starts with `[Source: <filename> | Link: <url>]`.
2. You MUST keep track of which source(s) and link(s) you used to generate your answer.
3. At the very end of your response, after a double newline, add a "Sources:" section.
4. List the unique sources you actually used as Markdown links: `[Filename](URL)`, separated by commas.
   Example: "Sources: [hr_policy_v1.pdf](http://lnk.to/1), [leave_manual.docx](http://lnk.to/2)"
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
