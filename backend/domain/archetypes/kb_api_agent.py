"""
Archetype 2 - Knowledge-Base + API agent graph (tool-calling supervisor).

Used by: Ask HR.

The LLM acts as a supervisor that decides which tool to call:
  • search_knowledge_base  → general HR policy questions
  • get_employee_leave_balance → personal leave data queries

Flow:
    START ──► agent (LLM supervisor) ──► tools_condition ──► tools ──► multimodal_check ──► agent ──► END
"""

import base64
import json
import logging
import os
import re

from langchain_core.messages import AIMessage, trim_messages, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from core.llm import get_chat_model
from domain.prompts import LANGUAGE_RULE
from domain.state import AgentState
from domain.tools.api_tools import _extract_sid_from_email, get_employee_leave_balance
from domain.tools.rag_tools import search_knowledge_base

log = logging.getLogger(__name__)

# Refusal shown when a user asks for someone else's leave balance.
_OTHER_EMPLOYEE_LEAVE_REFUSAL = (
    "Sorry, I can't do that. You can only view your own leave balance."
)
# A leave request is "for someone else" only if it mentions leave AND an
# employee ID that is not the caller's own.
_LEAVE_KEYWORDS = ("leave balance", "leave bal", "annual leave", "casual leave",
                   "sick leave", "leaves", "my leave", "leave")
_EMP_ID_RE = re.compile(r"\b\d{4,8}\b")


def encode_image_to_base64(image_path: str) -> str | None:
    """Helper to safely read and encode a local image file to Base64."""
    if not os.path.exists(image_path):
        log.warning(f"Evidence image missing on disk: {image_path}")
        return None
    try:
        with open(image_path, "rb") as img_file:
            encoded = base64.b64encode(img_file.read()).decode("utf-8")
            ext = os.path.splitext(image_path)[1].lower().replace(".", "")
            mime_type = "image/png" if ext == "png" else "image/jpeg"
            return f"data:{mime_type};base64,{encoded}"
    except Exception as err:
        log.error(f"Failed to encode image '{image_path}': {err}")
        return None


def _latest_human_text(messages: list) -> str:
    """Return the most recent human message as plain text."""
    for message in reversed(messages or []):
        if getattr(message, "type", None) == "human":
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _is_other_employee_leave_request(text: str, auth_sid: str) -> bool:
    """True when the user asks for a leave balance using an employee ID that
    is clearly NOT their own. Used to deterministically refuse (and avoid the
    LLM doing a confused two-pass tool-call + refusal, which streamed twice)."""
    lowered = text.lower()
    if not any(keyword in lowered for keyword in _LEAVE_KEYWORDS):
        return False
    auth_norm = auth_sid.lstrip("0")
    for found in _EMP_ID_RE.findall(text):
        if found.lstrip("0") != auth_norm:
            return True
    return False


# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()

# Bind BOTH tools so the LLM can choose which one to call
tools = [search_knowledge_base, get_employee_leave_balance]
llm_with_tools = llm.bind_tools(tools)


# ── Graph nodes ──────────────────────────────────────────────────────────
async def call_model(state: AgentState) -> dict:
    """Invoke the LLM with a strict HR-scoped system prompt."""
    via_supervisor = bool(state.get("via_supervisor"))

    # The authenticated caller's own employee ID, used so the agent can tell
    # a self leave-balance request apart from a request for someone else.
    auth_sid = _extract_sid_from_email(state.get("user_id", "")) or "unknown"

    # Deterministic privacy guard: if the user asks for someone else's leave
    # balance (an employee ID that is not their own), refuse immediately with a
    # single clean message — without invoking the tool-calling LLM. This both
    # enforces privacy reliably and avoids the model emitting the refusal twice.
    latest_human = _latest_human_text(state.get("messages", []))
    if auth_sid != "unknown" and _is_other_employee_leave_request(latest_human, auth_sid):
        return {"messages": [AIMessage(content=_OTHER_EMPLOYEE_LEAVE_REFUSAL)]}

    if via_supervisor:
        identity_block = """You are Workmate AI, SLTMobitel's unified internal assistant. The user does not know about any sub-agents or routing — they are talking to a single assistant called Workmate AI. For this turn, answer using HR knowledge. At SLTMobitel, HR covers Leave Policies, Employee Benefits, and all Staff Loans (Distress, Motorcycle, Car, Education).

GROUNDING (CRITICAL):
- Base factual answers strictly on the context returned by `search_knowledge_base`. Use retrieved context confidently when it directly mentions the entity the user asked about.
- Do NOT answer factual questions from your pre-trained general knowledge. Do NOT speculate or invent policy details.
- ANTI-ADJACENCY: If the user asks about an entity (an acronym, a policy name, a system name) and the retrieved context only mentions a DIFFERENT entity that looks similar or happens to appear nearby, DO NOT treat the nearby entity as the answer. Example: if the user asks about "SLTiDC" but the retrieved context only discusses "NIT" or "TDC", you must NOT equate them. Decline instead.
- Only decline by replying exactly "I don't have that information available." when the tool output is `[KB_UNAVAILABLE]`, the exact string "No relevant documents found.", or the retrieved context does not mention the user's exact entity/topic. Do NOT decline when the retrieved context clearly addresses the user's entity.

CONVERSATIONAL RULES:
- Respond naturally to greetings, thank-yous, goodbyes, and small talk. Be friendly and warm.
- Never introduce yourself as "Ask HR", an "HR specialist", or any department-specific assistant. You are Workmate AI.
- Never mention "different department", "different specialist agent", "another team", "Ask SLT agent", routing, or that multiple agents exist."""
    else:
        identity_block = """You are the Ask HR AI assistant for SLTMobitel.
Your primary purpose is to answer HR-related questions. At SLTMobitel, HR handles Leave Policies, Employee Benefits, and all Staff Loans (Distress, Motorcycle, Car, Education).
You handle internal corporate HR queries only.

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask HR assistant. How can I help you with HR-related queries today?"
- If the user asks about a completely unrelated department, politely decline and explain that a different specialist agent handles those topics."""

    system_prompt = f"""{identity_block}

STRICT RULES FOR FACTUAL QUESTIONS:
1. You have two tools: `search_knowledge_base` (for general HR policies and loan rules) and `get_employee_leave_balance` (for personal leave data).
2. You MUST ALWAYS use the `search_knowledge_base` tool to check for an answer BEFORE deciding to decline a question. Do not assume you know what is in the database.
3. DO NOT use your pre-trained general knowledge to answer factual or policy questions.
4. If the tools return no information after searching, or if the user asks about a completely unrelated department, you MUST decline politely.
5. If a tool returns an error, inform the user honestly that you could not retrieve the information. Do NOT fabricate data.
6. CRITICAL: When the context contains multiple items (like different types of loans or leaves), you MUST carefully isolate the specific item the user asked about.
7. LEAVE BALANCE PRIVACY: `get_employee_leave_balance` ONLY ever returns the leave balance of the currently authenticated (logged-in) user — it cannot look up anyone else. The authenticated user's own employee ID is {auth_sid}. When the user asks for a leave balance, decide as follows:
   - If they ask for their own leave (no ID given, "my leave balance", or they give an employee ID that MATCHES {auth_sid}), call the tool and show the result normally.
   - If they give an employee ID or name that clearly belongs to SOMEONE ELSE (i.e. an employee ID different from {auth_sid}), DO NOT call the tool and DO NOT show any leave balance. Reply ONLY with a clear refusal such as: "Sorry, I can't do that. You can only view your own leave balance." Do not append the authenticated user's balance to that refusal — it confuses the user into thinking they received the other person's data.
8. MULTI-QUESTION HANDLING: If the user's message contains more than one distinct question, call `search_knowledge_base` once per question using a focused, single-topic sub-query for each. Do NOT combine multiple questions into a single search — it degrades retrieval quality. Only search for sub-questions that clearly fall within HR scope (leave policies, loans, employee benefits, staff welfare). If a sub-question clearly belongs to another department (IT, Finance, Admin, CIA), skip it entirely — do not search for it and do not answer it. After all relevant searches complete, compose one unified response covering only the questions you found answers for. DO NOT mix up rules belonging to one item with another. Pay close attention to section headers like "[Section: ...]" in the retrieved context — they indicate which parent topic each chunk belongs to. Only use information from the section that matches the user's question. For example, if the user asks about Distress Loan, IGNORE any information from Motor Car Loan, Motorcycle Loan, or TDC Education Loan sections, even if those chunks appear in the results.

RESPONSE FORMATTING RULES:
0. LEAVE BALANCE FORMAT: When you call `get_employee_leave_balance`, reproduce its result as the same heading followed by one bullet per leave plan — keep EVERY plan, EVERY balance number, and EVERY entitlement number EXACTLY as returned. Do NOT summarize it into a sentence, drop or reorder any plan, or change any number. The only thing you MAY change is the LANGUAGE: translate the heading, the leave-plan names, and the "days remaining out of" wording into the user's language per the LANGUAGE rule below, while keeping all numeric values identical. The BLUF/conciseness rules below do NOT apply to leave balance output. Do not add a "Sources:" section for leave balance.
1. DIRECT ANSWER FIRST (BLUF): Always start your response with a direct, one-sentence answer to the user's specific question. Do not use filler phrases like "According to the policy..." or "Here are the guidelines...".
2. STRICTLY RELEVANT: Only answer exactly what the user asked. Do not add extra related policy details unless explicitly requested.
3. CONCISENESS: Prefer concise answers to improve response time and user experience. Use standard Markdown bullet points (`*` or `-`), starting each point on a NEW line.
4. BOLD KEY METRICS: Always bold crucial variables like times (e.g., **8.30 a.m.**), durations (e.g., **3.5 hours**), and quantities to make the text highly scannable.
5. MARKDOWN SPACING: Use a double newline (blank line) between the direct answer and the bulleted list to ensure proper rendering. Do NOT use non-standard bullet characters like `•`.
6. NO CLOSING QUESTIONS: Do not end your response with phrases like "Is there anything else I can help you with?". Just stop once the answer is complete.
7. WORD COUNT LIMIT: Keep normal factual answers under 300 words unless the user explicitly asks for a detailed explanation.
8. SENTENCE COUNT LIMIT: For simple policy questions, use maximum 7 short sentences or 7 bullet points.
9. NO OVER-ANSWERING: Do not include unrelated policy sections, examples, or extra explanations unless the user asks.
10. FINAL GROUNDING CHECK: Before finalizing, silently check that every factual claim, number, duration, condition, and exception appears in the retrieved context or tool result. If not, remove it.
11. UNSUPPORTED ANSWER RULE: If the retrieved context or tool result does not clearly support the answer, reply: "I don't have that information available."

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

    # ── Answer in the user's language ─────────────────────────────────
    system_prompt += f"\n\n{LANGUAGE_RULE}"

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


async def multimodal_check(state: AgentState) -> dict:
    """Check if the last tool call returned images; if so, re-invoke with multimodal."""
    messages = state["messages"]
    
    # Look for the most recent ToolMessage
    tool_message = None
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "tool":
            tool_message = msg
            break
    
    if not tool_message:
        return {}  # No tool message, nothing to do
    
    # Extract image_paths from tool result
    tool_result = tool_message.content
    if isinstance(tool_result, str):
        try:
            tool_result = json.loads(tool_result)
        except json.JSONDecodeError:
            log.warning("RAG tool returned non-JSON content; skipping multimodal analysis")
            tool_result = {}

    image_paths = tool_result.get("image_paths", []) if isinstance(tool_result, dict) else []
    
    # If no images, return as-is
    if not image_paths:
        return {}
    
    # Encode images
    encoded_images = []
    for path in image_paths:
        b64_str = encode_image_to_base64(path)
        if b64_str:
            encoded_images.append(b64_str)
    
    if not encoded_images:
        return {}  # Failed to encode images
    
    # Find the last user message
    user_messages = [m for m in messages if getattr(m, "type", None) == "human"]
    if not user_messages:
        return {}
    
    # Use multimodal LLM to process the context with images
    context_from_tool = tool_result.get("context", "")
    user_query = user_messages[-1].content if isinstance(user_messages[-1].content, str) else str(user_messages[-1].content)
    
    system_prompt = f"""You are analyzing retrieved HR context with visual evidence to answer a user question.
    
Retrieved Context:
{context_from_tool}

User Question: {user_query}

Analyze the provided images alongside the text context to give the most accurate answer. Be clear and concise."""
    
    # Build multimodal message
    content_blocks = [{"type": "text", "text": system_prompt}]
    for b64_img in encoded_images:
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": b64_img}
        })
    
    llm = get_chat_model()
    multimodal_message = HumanMessage(content=content_blocks)
    response = await llm.ainvoke([multimodal_message])
    
    # Replace the last AI message with the multimodal response
    new_messages = list(messages)
    for i in range(len(new_messages) - 1, -1, -1):
        if getattr(new_messages[i], "type", None) == "ai":
            new_messages[i] = response
            break
    
    return {"messages": new_messages}


# ── Build the (uncompiled) workflow ──────────────────────────────────────
def build_kb_api_workflow() -> StateGraph:
    """Return an uncompiled StateGraph - registry.py will compile it
    with the correct per-agent checkpointer."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("multimodal_check", multimodal_check)

    # Edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)   # → "tools" or END
    workflow.add_edge("tools", "multimodal_check")
    workflow.add_edge("multimodal_check", "agent")

    return workflow
