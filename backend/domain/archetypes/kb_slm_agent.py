"""
Ask HR SLM agent — demonstrates RAG using the company's internal SLM
(deepseek-r1:1.5b via Ollama) for both chat and embeddings.

Design notes:
- Tool-calling on a 1.5B reasoning model is unreliable, so retrieval
  runs unconditionally before the LLM call (single-node graph).
- DeepSeek-R1 prepends a <think>...</think> reasoning block to its
  output. The chat router strips that from the streamed tokens before
  they reach the user; here we just produce a clean prompt.
"""

from langchain_core.messages import HumanMessage, trim_messages
from langgraph.graph import END, START, StateGraph

from core.llm_slm import get_slm_chat_model
from domain.state import AgentState
from domain.tools.rag_tools_slm import search_hr_slm_kb

llm = get_slm_chat_model()


def _last_user_text(messages) -> str:
    for msg in reversed(messages):
        # Tuple form ("user", "text") or HumanMessage
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "user":
            return str(msg[1])
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


async def call_model(state: AgentState) -> dict:
    """Retrieve HR context, then ask the SLM to answer grounded in it."""
    user_query = _last_user_text(state["messages"])

    # Unconditional retrieval — no tool-calling loop with the 1.5B model.
    context = await search_hr_slm_kb.ainvoke({"query": user_query})

    system_prompt = f"""You are the Ask HR assistant for SLTMobitel, powered by an internal company-hosted small language model. Answer ONLY using the HR knowledge base context provided below.

CONTEXT FROM HR KNOWLEDGE BASE:
{context}

RULES:
1. If the context does not contain the answer, reply exactly: "I don't have that information in the HR knowledge base."
2. Do NOT use general knowledge. Stay strictly within the provided context.
3. Start with a direct one-sentence answer (BLUF), then optional bullets for details.
4. Bold key values (durations, dates, amounts) using **markdown**.
5. Be concise. No closing pleasantries like "let me know if...".
6. After your answer, add a "Sources:" line listing the unique [filename](link) pairs you actually used. Skip this section for greetings/small-talk.
7. Greetings and thank-yous: respond warmly and briefly without citing sources.
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
