"""Opt-in live checks using synthetic input. Never prints keys or prompt text."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from core.sentinel import SentinelChatModel, SentinelClient, SentinelEmbeddings, SentinelError
from core.vector_config import cloud_collection_name
from domain.guardrails import GuardrailResult


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", action="store_true", help="Send one small text request")
    parser.add_argument("--guardrail", action="store_true", help="Send one local-JSON classifier request")
    parser.add_argument("--embeddings", action="store_true", help="Send one embedding request")
    parser.add_argument("--rag", action="store_true", help="Embed two synthetic documents and a query, then search in-memory Qdrant")
    args = parser.parse_args()
    client = SentinelClient.from_settings(settings)
    payload, _ = client.request("/v1/models")
    allowed = {item.get("id") for item in payload.get("data", []) if isinstance(item, dict)}
    print(f"Model discovery succeeded ({len(allowed)} visible model IDs).")

    def require_model(model):
        if not model or model not in allowed:
            raise ValueError(f"Configured model is not listed for this key: {model or '(missing)'}")

    if args.chat:
        require_model(settings.SENTINEL_GATEWAY_MODEL)
        answer = SentinelChatModel(client=client, model=settings.SENTINEL_GATEWAY_MODEL, max_tokens=80).invoke(
            "Say hello in one short sentence.")
        print(f"Chat succeeded: finish={answer.response_metadata.get('finish_reason')}, request_id={answer.response_metadata.get('request_id')}")
    if args.guardrail:
        require_model(settings.SENTINEL_GUARDRAIL_MODEL)
        classifier = SentinelChatModel(client=client, model=settings.SENTINEL_GUARDRAIL_MODEL, max_tokens=300)
        from domain.guardrails import CLASSIFIER_PROMPT
        result = classifier.with_structured_output(GuardrailResult).invoke([
            ("system", CLASSIFIER_PROMPT), ("user", "Hello, where can I find the employee handbook?")])
        if result.action != "PASS":
            raise ValueError("Synthetic harmless guardrail fixture did not pass")
        print("Guardrail text JSON validated successfully.")
    if args.embeddings or args.rag:
        require_model(settings.SENTINEL_EMBEDDING_MODEL)
        embeddings = SentinelEmbeddings(client, settings.SENTINEL_EMBEDDING_MODEL,
                                         settings.SENTINEL_EMBEDDING_DIMENSIONS)
        if args.embeddings:
            vector = embeddings.embed_query("Solar panels convert sunlight into electricity.")
            print(f"Embedding succeeded: dimensions={len(vector)}")
        if args.rag:
            from qdrant_client import QdrantClient, models
            docs = ["Solar panels convert sunlight into electricity.", "Employee expenses require an itemized receipt."]
            vectors = embeddings.embed_documents(docs)
            query = embeddings.embed_query("How is sunlight converted to electrical energy?")
            qdrant = QdrantClient(":memory:")
            try:
                qdrant.create_collection("synthetic", vectors_config=models.VectorParams(
                    size=embeddings.dimensions, distance=models.Distance.COSINE))
                qdrant.upsert("synthetic", points=[models.PointStruct(id=i, vector=v) for i,v in enumerate(vectors)])
                hits = qdrant.query_points("synthetic", query=query, limit=1).points
                if not hits or hits[0].id != 0:
                    raise ValueError("Synthetic solar document was not the top retrieval result")
                print("Synthetic embedding/Qdrant retrieval succeeded; no production collections were modified.")
            finally:
                qdrant.close()
        print(f"Finance ingestion target: {cloud_collection_name('finance_docs', for_ingestion=True)}")
        print(f"Finance query target: {cloud_collection_name('finance_docs')}")


if __name__ == "__main__":
    try:
        main()
    except (SentinelError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
