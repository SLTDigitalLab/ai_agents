"""In-memory LifeStore product catalog loader used for order product search/validation.

Loads the scraped catalog JSON (LIFESTORE_PRODUCTS_JSON) once and caches it in memory,
reloading automatically if the file's contents change on disk. If the file is missing
(e.g. local dev without a scrape), lookups gracefully return empty/False instead of
raising, so order submission isn't blocked when no catalog is configured.
"""

import json
import os
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()
_cached_mtime: Optional[float] = None
_products_by_id: dict[str, dict] = {}
_products: list[dict] = []


def _catalog_path() -> Optional[Path]:
    raw_path = os.getenv("LIFESTORE_PRODUCTS_JSON")
    if not raw_path:
        return None
    return Path(raw_path)


def _load_if_needed() -> None:
    global _cached_mtime, _products_by_id, _products

    path = _catalog_path()
    if not path or not path.exists():
        _products_by_id = {}
        _products = []
        return

    mtime = path.stat().st_mtime
    if mtime == _cached_mtime:
        return

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = data.get("products", []) if isinstance(data, dict) else data
    _products = [p for p in products if p.get("product_id") and p.get("name")]
    _products_by_id = {p["product_id"]: p for p in _products}
    _cached_mtime = mtime


def search_products(query: str, limit: int = 10) -> list[dict]:
    """Case-insensitive substring search over product names."""
    with _lock:
        _load_if_needed()
        if not query.strip():
            return []
        needle = query.strip().lower()
        matches = [p for p in _products if needle in p["name"].lower()]
        return matches[:limit]


def get_product_by_id(product_id: str) -> Optional[dict]:
    with _lock:
        _load_if_needed()
        return _products_by_id.get(product_id)


def catalog_available() -> bool:
    with _lock:
        _load_if_needed()
        return bool(_products)
