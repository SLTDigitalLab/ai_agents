import logging
import re
import base64
import os
from langchain_core.messages import AIMessage, trim_messages,HumanMessage
from langgraph.graph import END, START, StateGraph

from core.llm_slm import get_slm_chat_model
from domain.agents.askhr_slm.state import AskHRSLMAgentState
from domain.tools.rag_tools_slm import search_hr_slm_kb
from core.llm import get_chat_model

log = logging.getLogger(__name__)
slm_llm = get_slm_chat_model()
multimodal_llm = get_chat_model()


def encode_image_to_base64(image_path: str) -> str | None:
    """Helper to safely read and encode a local image file to Base64."""
    if not os.path.exists(image_path):
        log.warning(f"[kb_wf] Evidence image missing on disk: {image_path}")
        return None
    try:
        with open(image_path, "rb") as img_file:
            encoded = base64.b64encode(img_file.read()).decode("utf-8")
            ext = os.path.splitext(image_path)[1].lower().replace(".", "")
            mime_type = "image/png" if ext == "png" else "image/jpeg"
            return f"data:{mime_type};base64,{encoded}"
    except Exception as err:
        log.error(f"[kb_wf] Failed to encode image '{image_path}': {err}")
        return None



async def kb_retrieval_node(state):
    """Performs hybrid vector search and context-grounded SLM generation."""
    user_query = state["messages"][-1].content
    log.info(f"[kb_wf] Querying HR KB for: {user_query!r}")

    #Since this workflow connects to the HITL response "NO" and the high confidence KB query"  
    if state.get("is_leave_balance") is False :
        user_query = "What are the general leave policies and details for all types of leaves?"

    # Execute Hybrid RAG Search
    retrieval_res = await search_hr_slm_kb.ainvoke({"query": user_query})
    if isinstance(retrieval_res, dict):
        context = retrieval_res.get("context", "")
        image_paths = retrieval_res.get("image_paths", [])
    else:
        context = str(retrieval_res)
        image_paths = []
    context_block = f"CONTEXT FROM HR KNOWLEDGE BASE:\n{context}"

    rules = (
        'If the context does not contain the answer, reply exactly: '
        '"I don\'t have that information in the HR knowledge base." '
        "Do NOT use general knowledge. Base your entire answer strictly on the context."
    )

    system_prompt = f"""You are the Ask HR assistant for SLTMobitel.

{context_block}

RULES:
{rules}

FORMATTING:
1. Start with a direct one-sentence answer (BLUF), then optional bullets.
2. Bold key values using **markdown**.
3. Be concise. No closing pleasantries.
4. End with a "Sources:" line listing unique [filename](link) pairs actually used.
"""

    trimmed_messages = trim_messages(
        state["messages"],
        max_tokens=8,
        strategy="last",
        token_counter=len,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )

    # Build Multimodal Payload if Image Paths exist
    encoded_images = []
    for path in image_paths:
        b64_str = encode_image_to_base64(path)
        if b64_str:
            encoded_images.append(b64_str)

    if encoded_images:
        log.info(f"[kb_wf] Routing query through GPT-4o with {len(encoded_images)} evidence image(s).")

        # Construct multimodal content blocks for GPT-4o
        content_blocks = [{"type": "text", "text": f"{system_prompt}\n\nUser Question: {user_query}"}]

        for b64_img in encoded_images:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": b64_img}
            })

        multimodal_user_message = HumanMessage(content=content_blocks)
        response = await multimodal_llm.ainvoke([multimodal_user_message])
    else:
        # Standard text path using SLM
        messages = [{"role": "system", "content": system_prompt}] + trimmed_messages
        response = await slm_llm.ainvoke(messages)

    return {
        "messages": [response],
        "image_paths": image_paths
    }

# Workflow Assembly
kb_builder = StateGraph(AskHRSLMAgentState)
kb_builder.add_node("kb_retrieval", kb_retrieval_node)

kb_builder.add_edge(START, "kb_retrieval")
kb_builder.add_edge("kb_retrieval", END)

# Compiled child graph
kb_subgraph = kb_builder.compile()


# ── RAG Workflow ──────────────────────────────────────
# Convert the question into an embedding.
# Search a vector database (such as Qdrant).
# Find the most relevant HR documents.
# Return them.

#START -> kb_retrieval_node -> END