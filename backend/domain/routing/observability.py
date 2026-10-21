"""Correlated supervisor events for the backend's existing INFO log stream."""
import hashlib
import json
import logging

from core.config import settings
from core.privacy import mask_pii

logger = logging.getLogger("domain.supervisor")


def query_preview(query: str) -> dict:
    if settings.SUPERVISOR_ROUTING_LOG_TEXT:
        return {"question_preview": mask_pii(query)[:500]}
    return {}


def emit_event(state: dict, event: str, *, level: int = logging.INFO, **fields) -> None:
    metadata = state.get("routing_metadata") or {}
    thread_id = state.get("thread_id")
    payload = {"event": event, "trace_id": metadata.get("trace_id"),
               "conversation_id": hashlib.sha256(str(thread_id).encode()).hexdigest()[:16] if thread_id else None,
               **fields}
    logger.log(level, "Supervisor trace | %s", json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
