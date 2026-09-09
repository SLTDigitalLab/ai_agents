"""Collection isolation for Sentinel embeddings; SLM names remain unchanged."""
import re
import hashlib
import json
import uuid

from core.config import settings


def validate_sentinel_vector_config():
    if not settings.SENTINEL_EMBEDDING_MODEL or not settings.SENTINEL_EMBEDDING_DIMENSIONS:
        raise ValueError("Configure the approved SENTINEL_EMBEDDING_MODEL and SENTINEL_EMBEDDING_DIMENSIONS")
    if settings.SENTINEL_REUSE_EXISTING_VECTORS:
        if settings.SENTINEL_EMBEDDING_DIMENSIONS != settings.EMBEDDING_DIMENSIONS:
            raise ValueError("Existing-vector reuse requires the same EMBEDDING_DIMENSIONS")
    elif not re.fullmatch(r"[a-zA-Z0-9_-]+", settings.SENTINEL_COLLECTION_PREFIX):
        raise ValueError("Set a non-empty SENTINEL_COLLECTION_PREFIX for the new vector space")


def uses_sentinel_embeddings(for_ingestion=False):
    return settings.EMBEDDING_PROVIDER.lower().strip() == "sentinel" or (
        for_ingestion and settings.SENTINEL_STAGE_EMBEDDINGS
    )


def cloud_collection_name(name, *, for_ingestion=False):
    if name == "askhrslm_docs" or not uses_sentinel_embeddings(for_ingestion):
        return name
    validate_sentinel_vector_config()
    if settings.SENTINEL_STAGE_EMBEDDINGS and settings.SENTINEL_REUSE_EXISTING_VECTORS:
        raise ValueError("Staged ingestion must use isolated collections, not existing-vector reuse")
    if settings.SENTINEL_REUSE_EXISTING_VECTORS:
        return name
    identity = f"{settings.SENTINEL_EMBEDDING_MODEL}:{settings.SENTINEL_EMBEDDING_DIMENSIONS}:chunks-v1"
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()[:10]
    prefix = f"{settings.SENTINEL_COLLECTION_PREFIX}{fingerprint}_"
    return name if name.startswith(prefix) else prefix + name


def cloud_embedding_dimensions(*, for_ingestion=False):
    if uses_sentinel_embeddings(for_ingestion):
        validate_sentinel_vector_config()
        return settings.SENTINEL_EMBEDDING_DIMENSIONS
    return settings.EMBEDDING_DIMENSIONS


def ingestion_collection_name(agent_name):
    # MCP may supply lifestore_docs while the admin UI supplies lifestore.
    # Both must target the same isolated collection during a migration.
    if uses_sentinel_embeddings(for_ingestion=True) and agent_name.endswith("_docs"):
        name = agent_name
    else:
        name = f"{agent_name}_docs"
    return cloud_collection_name(name, for_ingestion=True)


def sentinel_document_ids(documents):
    """Stable upsert IDs and vector-space provenance for migration retries."""
    if not uses_sentinel_embeddings(for_ingestion=True):
        return None
    ids = []
    for document in documents:
        metadata = document.metadata
        identity = json.dumps({"text": document.page_content,
                               "source": metadata.get("onedrive_id") or metadata.get("link") or metadata.get("source"),
                               "page": metadata.get("page")}, sort_keys=True, default=str)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        metadata.update(embedding_model=settings.SENTINEL_EMBEDDING_MODEL,
                        embedding_dimensions=settings.SENTINEL_EMBEDDING_DIMENSIONS,
                        chunking_version="chunks-v1", chunk_id=point_id)
        ids.append(point_id)
    return ids
