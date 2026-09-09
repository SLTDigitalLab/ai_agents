"""Compare existing OpenAI embeddings with Sentinel, without changing vectors.

Only synthetic text is sent to either provider. Existing Qdrant payloads are
neither loaded nor sent upstream. Configuration is never changed by this script.
"""
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from core.sentinel import SentinelClient, SentinelEmbeddings, SentinelError
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient


def cosine(left, right):
    return sum(a*b for a,b in zip(left, right)) / math.sqrt(
        sum(a*a for a in left) * sum(b*b for b in right))


def main():
    texts = [
        "Where can employees find the annual leave policy?",
        "Solar panels convert sunlight into electricity.",
        "Which wireless routers are available in the product catalog?",
    ]
    direct = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        api_key=settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY,
        base_url=settings.EMBEDDING_BASE_URL,
        max_retries=0, request_timeout=45,
    )
    gateway = SentinelEmbeddings(SentinelClient.from_settings(settings),
                                 settings.SENTINEL_EMBEDDING_MODEL,
                                 settings.SENTINEL_EMBEDDING_DIMENSIONS)
    print("Comparing three synthetic samples with the configured direct OpenAI model.", flush=True)
    original = direct.embed_documents(texts)
    print("Direct OpenAI embedding request succeeded.", flush=True)
    proxied = gateway.embed_documents(texts)
    print("Sentinel embedding request succeeded.", flush=True)
    comparisons = []
    for index, (left, right) in enumerate(zip(original, proxied)):
        if len(left) != len(right) or len(left) != settings.EMBEDDING_DIMENSIONS:
            raise ValueError("Direct and gateway vector dimensions differ")
        similarity = cosine(left, right)
        difference = max(abs(a-b) for a,b in zip(left, right))
        comparisons.append({"sample":index+1,"dimensions":len(left),
                            "cosine_similarity":round(similarity,12),
                            "max_absolute_difference":difference})
    compatible = all(item["cosine_similarity"] >= 0.99999 and item["max_absolute_difference"] <= 0.0001
                     for item in comparisons)
    print(json.dumps({"embedding_comparison":"PASS" if compatible else "MISMATCH","samples":comparisons}), flush=True)
    if not compatible:
        raise ValueError("Embedding equivalence check failed; do not reuse existing vectors")

    store = QdrantClient(url=settings.QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY") or None,
                        timeout=10, check_compatibility=False)
    tested = []
    try:
        collections = store.get_collections().collections
        for collection in collections:
            if "slm" in collection.name.lower() or collection.name.startswith("sentinel_"):
                continue
            info = store.get_collection(collection.name)
            config = info.config.params.vectors
            vector_name = "dense" if isinstance(config, dict) and "dense" in config else None
            vector_config = config.get("dense") if isinstance(config, dict) else config
            if vector_config is None or vector_config.size != len(original[0]) or not info.points_count:
                continue
            results = []
            for old_query, new_query in zip(original, proxied):
                options = dict(collection_name=collection.name, limit=5,
                               with_payload=False, with_vectors=False)
                if vector_name:
                    options["using"] = vector_name
                before = store.query_points(query=old_query, **options).points
                after = store.query_points(query=new_query, **options).points
                old_ids, new_ids = [p.id for p in before], [p.id for p in after]
                if not old_ids or old_ids != new_ids:
                    raise ValueError("Existing collection retrieval rankings differ; investigate before switching")
                results.append({"same_ranked_results":True,"result_count":len(after)})
            tested.append({"collection":collection.name,"point_count":info.points_count,"queries":results})
            if len(tested) >= 2:
                break
        print(json.dumps({"existing_collection_checks":tested,
                          "status":"PASS" if tested else "NO_POPULATED_COMPATIBLE_COLLECTION"}), flush=True)
        if not tested:
            return 2
    except ValueError:
        raise
    except Exception as exc:
        # Network exceptions may contain URL credentials. Report only the type.
        print(json.dumps({"existing_collection_check":"UNAVAILABLE","error_type":type(exc).__name__}), flush=True)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SentinelError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Compatibility check failed ({type(exc).__name__}); configuration unchanged.", file=sys.stderr)
        sys.exit(1)
