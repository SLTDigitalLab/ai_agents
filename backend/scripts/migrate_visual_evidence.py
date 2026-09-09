"""Migrate legacy visual assets and backfill unambiguous Qdrant image paths.

Run from the backend directory after backing up Qdrant:
    python scripts/migrate_visual_evidence.py
"""

import logging
import shutil
import sys
from pathlib import Path

from qdrant_client import QdrantClient, models

# Allow `python scripts/migrate_visual_evidence.py` from the backend directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.config import BACKEND_DIR, ROOT_DIR, evidence_storage_dir, settings

log = logging.getLogger(__name__)


def _copy_legacy_assets(destination: Path) -> int:
    copied = 0
    legacy_dirs = (ROOT_DIR / "storage" / "visuals", BACKEND_DIR / "storage" / "visuals")

    for legacy_dir in legacy_dirs:
        if not legacy_dir.is_dir():
            continue

        for asset in legacy_dir.glob("*.png"):
            target = destination / asset.name
            if target.exists():
                continue
            shutil.copy2(asset, target)
            copied += 1

    return copied


def _find_asset(destination: Path, payload: dict) -> Path | None:
    doc_id = str(payload.get("doc_id") or "")
    page = payload.get("page_number") or payload.get("page")
    if not doc_id:
        return None

    candidates = list(destination.glob(f"{doc_id}_*.png"))
    if page is not None:
        page_text = str(page)
        candidates = [
            candidate
            for candidate in candidates
            if f"_page_{page_text}." in candidate.name
            or f"_slide_{page_text}." in candidate.name
            or f"_{page_text}_" in candidate.name
        ]

    return candidates[0] if len(candidates) == 1 else None


def migrate() -> tuple[int, int, int]:
    """Return copied assets, updated Qdrant points, and ambiguous records."""
    destination = evidence_storage_dir()
    destination.mkdir(parents=True, exist_ok=True)
    copied = _copy_legacy_assets(destination)
    updated = 0
    ambiguous = 0

    client = QdrantClient(url=settings.QDRANT_URL)
    for collection in client.get_collections().collections:
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=collection.name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="type",
                            match=models.MatchValue(value="visual_description"),
                        )
                    ]
                ),
                with_payload=True,
                with_vectors=False,
                offset=offset,
                limit=100,
            )

            for point in points:
                payload = point.payload or {}
                if payload.get("image_path"):
                    continue

                asset = _find_asset(destination, payload)
                if asset is None:
                    ambiguous += 1
                    log.warning(
                        "Cannot safely backfill image_path | collection=%s | point=%s | doc_id=%s",
                        collection.name,
                        point.id,
                        payload.get("doc_id"),
                    )
                    continue

                client.set_payload(
                    collection_name=collection.name,
                    payload={"image_path": str(asset), "has_image": True},
                    points=[point.id],
                )
                updated += 1

            if offset is None:
                break

    return copied, updated, ambiguous


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    copied_count, updated_count, ambiguous_count = migrate()
    log.info(
        "Migration complete | copied_assets=%s | updated_points=%s | requires_reingestion=%s",
        copied_count,
        updated_count,
        ambiguous_count,
    )