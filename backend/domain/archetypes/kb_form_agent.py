"""
Archetype 3 - Knowledge-Base + Form (Generative UI) agent graph.

Used by: Ask Lifestore, Ask Enterprise.

The LLM answers product/service questions via RAG and triggers a frontend
form when the user wants to buy, order, or subscribe. No backend state
machine is needed — the React frontend handles the form rendering when
it detects the ``[RENDER_*_FORM]`` token in the response.

Flow:
    START ──► agent (LLM) ──► tools_condition ──► tools (RAG) ──► multimodal_check ──► agent ──► END
"""

import base64
import json
import logging
import os
from langchain_core.messages import trim_messages, HumanMessage, AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import settings
from core.llm import get_chat_model
from domain.prompts import LANGUAGE_RULE
from domain.state import AgentState
from domain.tools.rag_tools import search_knowledge_base

log = logging.getLogger(__name__)

# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()

# Bind the RAG tool
tools = [search_knowledge_base]
llm_with_tools = llm.bind_tools(tools)


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
Your primary purpose is to answer questions related to {agent_id} products and services.
You handle {agent_id} queries only.

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask {agent_id.upper()} assistant. How can I help you today?"
- If the user asks about a completely unrelated department, politely decline and explain that a different specialist agent handles those topics.

STRICT RULES FOR FACTUAL QUESTIONS:
1. You MUST use `search_knowledge_base` to answer general questions about products, services, or pricing.
2. DO NOT use your pre-trained general knowledge to answer factual or product questions.
3. If the tool returns an empty result, or if the retrieved context does not clearly contain the answer, you MUST decline to answer.
4. If a tool returns an error, inform the user honestly that you could not retrieve the information. Do NOT fabricate data.
5. FORM TRIGGER RULE:
   Append {form_token} ONLY when the user's latest message directly and clearly expresses that they want to proceed with a purchase, order, subscription, application, registration, or service request.

   Append {form_token} for clear intent such as:
   - "I want to buy this"
   - "I need to buy a TV"
   - "I want to order this product"
   - "I want to purchase this"
   - "I want to subscribe to this package"
   - "I need a new connection"
   - "I want to apply for this service"
   - "Register me for this"
   - "How can I buy this?"
   - "How can I order this?"

   Do NOT append {form_token} for informational, comparison, recommendation, stock, seller, price, feature, or availability questions.

   Do NOT append {form_token} for questions such as:
   - "What is the price?"
   - "Is this in stock?"
   - "Is this available?"
   - "Who is the seller?"
   - "What are the features?"
   - "Tell me about this product"
   - "Compare these products"
   - "Recommend a router"
   - "Do you have TVs?"
   - "What products are available?"

   Do NOT end informational answers with purchase-suggestion sentences such as "If you want, I can help you proceed with the purchase request."

   If the user asks only for information, answer descriptively and stop.

   If the user clearly wants to buy/order/subscribe/apply/register, append exactly this token at the very end of the response:
   {form_token}
6. Do NOT ask the user for their name, NIC, or details in the chat. The form will handle that.
7. CRITICAL: When the context contains multiple items, you MUST carefully isolate the specific item the user asked about. DO NOT mix up details belonging to one product with another.

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST (BLUF):
   Start with a direct answer to the user’s exact question in 1–2 sentences.

2. DESCRIPTIVE ANSWERS:
   After the direct answer, provide a helpful descriptive explanation using relevant details from the retrieved knowledge base. Do not make answers overly short when the context contains useful supporting information.

3. INCLUDE RELEVANT CONTEXT:
   Depending on the user’s question, include useful details such as:
   - product/service name
   - seller/provider
   - price
   - stock status or availability
   - category or product type
   - key features
   - package details
   - important limitations or conditions
   - comparison points if the user asks to compare

4. STAY RELEVANT:
   Descriptive does not mean unrelated. Only include details that help answer the user’s question. Do not add unrelated sales suggestions, form instructions, or purchase prompts unless the user explicitly asks to buy/order/subscribe/apply/register.

5. STRUCTURED FORMAT:
   Use clear Markdown formatting:
   - Use standard bullets (`-`)
   - Use short section headings in **bold** when helpful
   - Use tables for comparisons or multi-product answers
   - Keep paragraphs short and readable

6. BOLD IMPORTANT VALUES:
   Bold important details such as prices, speeds, durations, quantities, seller names, and stock status.
   Examples:
   - **Rs. 15,260.00**
   - **100 Mbps**
   - **in_stock**
   - **out_of_stock**
   - **SLT-MOBITEL**

7. PRODUCT ANSWER STYLE:
   For product questions, use this structure when relevant:

   Direct answer sentence.

   - Product: **<product name>**
   - Seller: **<seller>**
   - Price: **<price>**
   - Stock status: **<stock_status>**
   - Category: **<category>**
   - Key details: <brief useful description>

   Include only fields that are relevant and available in the retrieved context.

8. SERVICE/PACKAGE ANSWER STYLE:
   For service or package questions, use this structure when relevant:

   Direct answer sentence.

   **Overview**
   - Explain what the service/package is.

   **Key details**
   - Include prices, speeds, data limits, validity periods, or conditions if available.

   **Important notes**
   - Mention limitations, eligibility, or setup requirements only if they appear in the retrieved context.

9. COMPARISONS:
   If the user asks to compare products/services, use a Markdown table when it improves clarity.

10. GROUNDEDNESS:
   Every factual claim, number, price, stock status, seller, package feature, or condition must come from the retrieved knowledge base. Do not invent missing details.

11. NO FORM SUGGESTIONS IN INFORMATIONAL ANSWERS:
   Do not end informational answers with:
   - "If you want, I can help you proceed with the purchase request."
   - "Would you like to buy it?"
   - "I can open a form for you."
   - "I can help you order this."

12. NO CLOSING QUESTIONS:
   Do not end with "Is there anything else I can help you with?" or similar closing questions. Stop once the answer is complete.

CITATIONS:
1. You may see `[Source: ... | Link: ...]` tags in the retrieved context.
2. You MUST IGNORE these tags.
3. DO NOT include any "Sources:" section or links in your response.

Example Informational Response:
User: "Do you have TVs?"
Assistant: "Yes, LIFESTORE has TV-related products available."

Example Purchase Response:
User: "I need to buy a TV."
Assistant: "I can help you start the purchase request for the TV."

{form_token}
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
    
    system_prompt = f"""You are analyzing retrieved product/service context with visual evidence to answer a user question.
    
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
def build_kb_form_workflow() -> StateGraph:
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
