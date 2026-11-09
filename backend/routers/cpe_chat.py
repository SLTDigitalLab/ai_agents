"""
Ask CPE Router - Dedicated endpoint cloned from Ask LifeStore blueprint.
Provides enterprise network engineering answers, inventory lookups, and hardware cards.
Features full PostgreSQL multi-turn conversation memory and contextual query expansion.

Endpoints:
- POST /api/v1/cpe/chat
- POST /api/v1/cpe/mcp-chat (LifeStore compatibility alias)
- GET  /api/v1/cpe/history/{thread_id}
"""

import logging
import os
import re
import time
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain_core.messages import SystemMessage, HumanMessage

from core.config import settings
from core.llm import get_ingestion_embedding_model, get_chat_model
from services.cpe_memory import (
    get_cpe_langchain_history,
    save_cpe_turn,
    contextualize_cpe_query,
    get_cpe_history_for_frontend,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cpe", tags=["Ask CPE"])

COLLECTION_NAME = "cpe_docs"

_VECTOR_STORE: Optional[QdrantVectorStore] = None


def get_cpe_vector_store() -> QdrantVectorStore:
    """Singleton connection to Qdrant 'cpe_docs' collection."""
    global _VECTOR_STORE
    if _VECTOR_STORE is not None:
        return _VECTOR_STORE

    qdrant_url = getattr(settings, "QDRANT_URL", "http://slt_qdrant:6333")
    client = None
    for candidate in [qdrant_url, "http://localhost:6335", "http://localhost:6333"]:
        try:
            c = QdrantClient(url=candidate, timeout=30, check_compatibility=False)
            c.get_collections()
            client = c
            break
        except Exception:
            continue

    if not client:
        raise RuntimeError("Could not connect to Qdrant for CPE vector store.")

    embedding_model = get_ingestion_embedding_model()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

    _VECTOR_STORE = QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=embedding_model,
        sparse_embedding=sparse_embeddings,
        retrieval_mode=RetrievalMode.HYBRID,
    )
    return _VECTOR_STORE


class CPEChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User's query about CPE/NTU hardware or stock")
    thread_id: Optional[str] = Field(default=None, description="Conversation thread identifier")
    agent_id: str = "cpe"
    limit: int = Field(default=8, ge=1, le=16)


class CPEHardwareCard(BaseModel):
    name: str
    vendor: Optional[str] = "SLT Enterprise"
    category: Optional[str] = "Enterprise CPE"
    item_code: Optional[str] = None
    specs: Optional[str] = None
    warehouse: Optional[str] = "Peliyagoda Main Stores"
    source: Optional[str] = None


class CPEChatResponse(BaseModel):
    status: str = "success"
    answer: str
    thread_id: Optional[str] = None
    hardware_cards: List[CPEHardwareCard] = Field(default_factory=list)
    retrieved_count: int = 0


def _filter_relevant_hardware_cards(
    cards: List[CPEHardwareCard], answer_text: str, query: str
) -> List[CPEHardwareCard]:
    """Filter hardware reference cards to only display hardware actually mentioned in the response or query."""
    ans_l = answer_text.lower()
    q_l = query.lower()

    filtered = []
    for card in cards:
        c_name = card.name.lower()
        c_code = (card.item_code or "").lower()

        # Exclude peripheral transceivers/cables unless user explicitly asked about transceivers/SFP/optics
        if any(w in c_name for w in ["transceiver", "sfp ehwic", "mm sfp", "port module", "cable"]):
            if not any(w in q_l for w in ["sfp", "transceiver", "module", "cable", "optic"]):
                continue

        # Item code match
        if c_code and c_code in ans_l:
            filtered.append(card)
            continue

        # Target model tokens
        keywords = [w for w in re.split(r"[\s/,\-]+", c_name) if len(w) >= 3]
        device_tokens = [
            w for w in keywords if any(
                k in w for k in [
                    "one421", "one521", "one2501", "1424", "s5735", "4321", "4331",
                    "1121", "srx", "c8200", "c8300", "fortigate", "c1300", "f670", "eg8145"
                ]
            )
        ]

        if device_tokens and any(tok in ans_l or tok in q_l for tok in device_tokens):
            filtered.append(card)
        elif any(w in ans_l for w in ["srx 300", "srx 320", "srx 340", "c1121x", "catalyst 1300"]) and any(w in c_name for w in ["srx", "1121", "1300"]):
            filtered.append(card)

    if filtered:
        return filtered[:5]

    # Clean fallback: exclude peripheral transceivers
    non_transceivers = [
        c for c in cards if not any(w in c.name.lower() for w in ["transceiver", "sfp ehwic", "mm sfp"])
    ]
    return non_transceivers[:3]


@router.get("/history/{thread_id}")
async def cpe_get_history(thread_id: str):
    """Retrieve full conversation history for this CPE thread."""
    messages = get_cpe_history_for_frontend(thread_id)
    return {"messages": messages}


@router.post("/chat", response_model=CPEChatResponse)
@router.post("/mcp-chat", response_model=CPEChatResponse)
async def cpe_chat(request: CPEChatRequest):
    """
    Handle CPE queries: technical specs, Peliyagoda inventory, hardware comparisons.
    Maintains full PostgreSQL multi-turn memory and contextual retrieval.
    """
    start_time = time.time()
    query = request.message.strip()
    thread_id = request.thread_id or f"cpe_{int(time.time())}"

    # 1. Multi-turn contextual query expansion (resolves pronouns like 'it', 'its', 'the router')
    retrieval_query = contextualize_cpe_query(query, thread_id)

    # Dynamic k: broad category queries need more chunks to capture complete multi-vendor portfolios
    retrieval_k = request.limit
    if re.search(r"\b(sdwan|sd-wan|options|portfolio|models available|all switches|all routers|all devices|inventory list)\b", query, re.I):
        retrieval_k = max(retrieval_k, 14)

    try:
        store = get_cpe_vector_store()
        docs = store.similarity_search(retrieval_query, k=retrieval_k)
    except Exception as exc:
        logger.exception("CPE retrieval failed: %s", exc)
        return CPEChatResponse(
            status="error",
            answer=f"Could not connect to the CPE knowledge base: {exc}",
            thread_id=thread_id,
        )

    if not docs:
        fallback_answer = "I searched the Enterprise CPE database, but no matching hardware, inventory, or datasheets were found for your query."
        save_cpe_turn(thread_id, query, fallback_answer, [])
        return CPEChatResponse(
            status="success",
            answer=fallback_answer,
            thread_id=thread_id,
            retrieved_count=0,
        )

    # 2. Format context and extract structured device cards
    context_blocks = []
    hardware_cards: List[CPEHardwareCard] = []
    seen_names = set()

    for d in docs:
        context_blocks.append(d.page_content)
        meta = d.metadata or {}
        device_name = meta.get("device") or meta.get("model") or meta.get("name")
        if device_name and device_name not in seen_names:
            seen_names.add(device_name)
            hardware_cards.append(
                CPEHardwareCard(
                    name=device_name,
                    vendor=meta.get("vendor"),
                    category=meta.get("category") or meta.get("doc_type"),
                    item_code=meta.get("item_code"),
                    specs=meta.get("feature") or meta.get("diff"),
                    warehouse=meta.get("warehouse", "Peliyagoda Main Stores"),
                    source=meta.get("source"),
                )
            )

    context_str = "\n\n---\n\n".join(context_blocks)

    system_prompt = (
        "You are Ask CPE, SLTMobitel's Enterprise Customer Premises Equipment (CPE) & NTU assistant.\n"
        "You assist enterprise network engineers, solution architects, and sales teams with verified hardware information.\n\n"
        "STRICT GROUNDING & BEHAVIOR RULES:\n"
        "1. GROUNDING & NO HALLUCINATIONS: Base your answer strictly on the provided verified context. NEVER guess, invent, or extrapolate specs, item codes (e.g. CPEDAT... / NWK...), part numbers, or stock quantities. If an item code or stock count is not in the verified context, explicitly state that it is not documented in the inventory records.\n"
        "2. INVENTORY & AVAILABILITY: If asked about inventory or availability, state exact stock details listed for Peliyagoda Main Stores, including Item Code, On Hand, Repaired, and Regional stock as documented. If an item code is listed under multiple categories (such as Cisco C1121X-8P NWKEAC084 under Cisco Routers: 22 on hand vs SD-WAN Edges: 114 on hand), explicitly report both numbers.\n"
        "3. HARDWARE ATTRIBUTES & COMPARATIVE TABLES: Highlight key port counts (e.g. 10G SFP+, Gigabit Ethernet), PoE power budgets, and device roles (L2/L3, router, switch, firewall, IP Phone). When extracting specs from comparative tables (e.g. Huawei S5735-L models or Cisco ISR 4000 series), verify that each value (PoE budget, forwarding rate, switching capacity, NIM/SM-X slots, memory) corresponds strictly to the exact requested model suffix (e.g. L8P4S has 124W PoE, L24P4S has 380W PoE; ISR 4321 has 2 NIM slots and 0 SM-X slots). Do not conflate adjacent model rows.\n"
        "4. PoE SUPPORT & 'VERIFY VARIANT': If a specification or matrix field states 'Verify variant' (such as for PoE on ONE421 or ONE521), you MUST state that it is variant-dependent and unconfirmed in the matrix. NEVER confirm PoE support or say 'Yes' without an explicit datasheet PoE wattage/port specification (such as ONE2501 120W PoE or Huawei S5735-L 124W/380W).\n"
        "5. FALSE PREMISES: If a question assumes something incorrect (e.g., asking if a device has a feature/port count it does not have), explicitly clarify and correct the false assumption before providing accurate specifications.\n"
        "6. CONFLICTING SOURCES & DISCREPANCIES (CRITICAL): Whenever the retrieved context mentions different values, contradictory claims, or ambiguous names for the same device or metric (e.g., OneAccess 1424 speed: 22 Mbps datasheet vs 2 Mbps slides/matrix; ONE521 throughput: 250 Mbps matrix vs 1 Gbps / 500 Mbps slides; Cisco ISR 4321 retirement status; Cisco 4231X vs 4321X replacement name; C1121X dual inventory listings): You MUST include a dedicated bullet or note explicitly titled 'Source Discrepancy / Conflict:' that calls out the mismatch, cites the conflicting figures alongside their respective sources, and clarifies the authoritative figure.\n"
        "7. UNANSWERABLE & ABSENCE CLAIMS: If the requested information (such as rental prices, purchase costs, warranty periods, ONT Wi-Fi capabilities, or unprovided vendor specs) is NOT present in the provided context, state clearly: 'The provided enterprise documentation does not specify...'. NEVER make negative absolute assertions (e.g., claiming a device 'does not have Wi-Fi' or 'does not support a feature') unless the documentation explicitly states 'No', 'None', or 'PoE Not Support'.\n"
        "8. ENTERPRISE REPLACEMENT POLICY & MULTI-HOP: For CPE replacement or migration questions, strictly follow the SLT Enterprise CPE Plan 2025 replacement matrix and flowchart (e.g., Cisco 891F/1921 -> ONE421, Cisco 1941/ISR 4200/4300 -> ONE521, Cisco 4400 -> ONE2501; higher performance -> Juniper SRX320/SRX340; Brand-specific Cisco -> Cisco C1121X-8P / 1121X or Cisco 4231X / ISR 4321X). If the query also asks about stock or availability of the replacement model, provide both the replacement model decision AND its Peliyagoda Main Stores inventory numbers.\n"
        "9. SERVICE MAPPINGS: If asked about service types mapped in the CPE Matrix (Sheet1), report the 4 core service types: DAB, Premium IPVPN, BIL, and E Net Access.\n"
        "10. MULTI-TURN CONVERSATION: Maintain awareness of prior discussion turns in the conversation. When answering follow-ups about previously discussed hardware, connect your response to the specific device context.\n"
        "11. FORMATTING: Be concise, structured, and use Markdown bullet points."
    )

    # 3. Pull recent multi-turn history from PostgreSQL
    history_messages = get_cpe_langchain_history(thread_id, max_turns=5)

    messages = [
        SystemMessage(content=f"{system_prompt}\n\n[VERIFIED CONTEXT]\n{context_str}"),
        *history_messages,
        HumanMessage(content=query),
    ]

    llm = get_chat_model()
    response = await llm.ainvoke(messages)
    answer_text = str(response.content)

    # 4. Post-generation Item Code Guard: verify that any item codes cited exist in the verified context
    mentioned_codes = set(re.findall(r"\b(?:CPEDAT\w+|NWK\w+|BNWK\w+)\b", answer_text, re.IGNORECASE))
    for code in mentioned_codes:
        if code.upper() not in context_str.upper():
            logger.warning("Post-generation guard detected unverified item code in LLM output: %s", code)
            answer_text += f"\n\n> [!NOTE]\n> Item code verification: Stock record for '{code}' was not found in the verified inventory list."

    # 5. Filter hardware cards to only relevant models cited in the answer
    selected_cards = _filter_relevant_hardware_cards(hardware_cards, answer_text, query)
    cards_dicts = [card.model_dump() for card in selected_cards]

    # 6. Atomically persist conversation turn in PostgreSQL
    save_cpe_turn(thread_id, query, answer_text, cards_dicts)

    return CPEChatResponse(
        status="success",
        answer=answer_text,
        thread_id=thread_id,
        hardware_cards=selected_cards,
        retrieved_count=len(docs),
    )
