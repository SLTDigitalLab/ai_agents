import logging
import asyncio
import numpy as np
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from core.llm_slm import get_slm_chat_model

log = logging.getLogger(__name__)

# 1. Load Sentence Transformer Model once at startup
embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# 2. Define Seed Clusters
INTENT_SEEDS = {
    "LEAVE_BALANCE": [
        "How many annual leave days do I have left?",
        "Check my personal leave balance",
        "What is my remaining sick leave allowance?",
        "Show my casual leave status from SLT ERP",
        "How much paid time off do I have left?"
    ],
    "KB_SEARCH": [
        "What is the company policy on medical leave?",
        "How do I submit an expense claim?",
        "What are the working hours during holiday periods?",
        "Tell me about the SLTMobitel maternity leave rules",
        "How does the performance review process work?"
    ],
    "GREETING": [
        "Hi",
        "Hello",
        "Good morning",
        "Hey there",
        "Good afternoon AskHR"
    ]
}

# Pre-compute seed embeddings
INTENT_EMBEDDINGS = {
    intent: embedding_model.encode(phrases)
    for intent, phrases in INTENT_SEEDS.items()
}

# Threshold Constants
HIGH_CONFIDENCE_THRESHOLD = 0.70  # Lowered from 0.82 so vector matches fast-track
LOW_CONFIDENCE_THRESHOLD = 0.45


# 3. Define Pydantic Schema for Structured Output from Ollama
class StructuredIntentResponse(BaseModel):
    intent: str = Field(
        description="The classified intent. Must be one of: LEAVE_BALANCE, KB_SEARCH, GREETING, or UNKNOWN"
    )
    confidence: float = Field(
        description="Confidence score between 0.0 and 1.0 based on semantic understanding"
    )


# 4. Hybrid Classifier Function
async def classify_intent_hybrid(user_query: str) -> tuple[str, float]:
    """Hybrid Classifier:
    1. Runs fast Vector Similarity check.
    2. If high confidence (>= 0.70), returns immediately.
    3. If ambiguous (0.45 <= score < 0.70), calls Ollama Structured Output for secondary opinion.
    4. If low similarity (< 0.45), returns UNKNOWN.
    """
    if not user_query.strip():
        return "UNKNOWN", 0.0

    # Step A: Vector Match (Sentence Transformer)
    query_embedding = await asyncio.to_thread(embedding_model.encode, [user_query])
    best_vector_intent = "UNKNOWN"
    highest_vector_score = 0.0

    for intent, seed_embeddings in INTENT_EMBEDDINGS.items():
        similarities = cosine_similarity(query_embedding, seed_embeddings)[0]
        max_sim = float(np.max(similarities))
        if max_sim > highest_vector_score:
            highest_vector_score = max_sim
            best_vector_intent = intent

    highest_vector_score = round(highest_vector_score, 3)
    log.info(f"[Vector Classifier] Query: {user_query!r} -> Intent: {best_vector_intent} ({highest_vector_score})")

    # High Confidence Cutoff: Fast Path
    if highest_vector_score >= HIGH_CONFIDENCE_THRESHOLD:
        return best_vector_intent, highest_vector_score

    # Low Confidence Cutoff: Direct Fallback Path
    if highest_vector_score < LOW_CONFIDENCE_THRESHOLD:
        return "UNKNOWN", highest_vector_score

    # Step B: Ambiguous Range (0.45 <= score < 0.70) -> Fallback to Ollama Structured SLM
    log.info(f"[Hybrid Router] Ambiguous vector score ({highest_vector_score}). Escalating to Ollama SLM Classifier...")

    try:
        slm = get_slm_chat_model().with_structured_output(StructuredIntentResponse)
        
        system_prompt = (
            "You are an intent classification assistant for an HR system.\n"
            "Classify the user's query into EXACTLY one of these intents:\n"
            "- LEAVE_BALANCE: User asking for their own personal remaining leave numbers or ERP balances.\n"
            "- KB_SEARCH: User asking about company policies, rules, working hours, or general benefits.\n"
            "- GREETING: Simple greetings or pleasantries.\n"
            "- UNKNOWN: Irrelevant, ambiguous, or out-of-scope requests.\n\n"
            "Provide an accurate confidence score (0.0 to 1.0)."
        )

        response: StructuredIntentResponse = await slm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ])

        log.info(f"[Ollama Classifier] SLM output -> Intent: {response.intent} ({response.confidence})")
        return response.intent, round(response.confidence, 3)

    except Exception as e:
        log.error(f"[Ollama Classifier Failed] Falling back to vector decision. Error: {e}")
        return best_vector_intent, highest_vector_score














