from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import asyncio
import logging
import requests

from collections import deque
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

from services.ingestion import IngestionService
from services import ingestion_status
from domain.tools.api_tools import LEAVE_BALANCE_API_URL


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
ingestion_service = IngestionService()


class UrlIngestRequest(BaseModel):
    # Old format for single URL ingestion
    url: Optional[str] = None
    agent_name: Optional[str] = None

    # New format for crawler-style ingestion
    urls: Optional[list[str]] = None
    collection_name: Optional[str] = None
    chunk_size: int = 1000
    chunk_overlap: int = 200
    crawl: bool = False
    max_pages: int = 200
    max_depth: int = 3


class OneDriveIngestRequest(BaseModel):
    folder_id: str
    token: str
    agent_name: str
    force: bool = False


class TestLeaveBalanceRequest(BaseModel):
    sid: str


def _normalize_url(url: str) -> str:
    """
    Remove fragments and trailing slash to avoid duplicate URLs.
    Example:
    https://site.com/product/1#reviews -> https://site.com/product/1
    """
    clean_url, _ = urldefrag(url)
    return clean_url.rstrip("/")


def _fetch_html_sync(url: str) -> str:
    """
    Fetch raw HTML from a URL.
    This function is called inside asyncio.to_thread() so it does not block FastAPI.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def _extract_links(html: str, base_url: str) -> list[str]:
    """
    Extract all valid links from a page.
    """
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for tag in soup.find_all("a", href=True):
        href = tag.get("href")

        if not href:
            continue

        href = href.strip()

        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        absolute_url = urljoin(base_url, href)
        absolute_url = _normalize_url(absolute_url)

        links.append(absolute_url)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(links))


def _is_allowed_url(url: str, seed_domains: set[str]) -> bool:
    """
    Only allow useful internal URLs.

    For LifeStore:
    - /products
    - /product
    - /categories
    - /category

    For SLT:
    - /en/business
    - /index.php/en/business
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path or "/"

    if domain not in seed_domains:
        return False

    allowed_prefixes_by_domain = {
        "lifestore.lk": ["/categories", "/category", "/product", "/products"],
        "www.lifestore.lk": ["/categories", "/category", "/product", "/products"],
        "slt.lk": ["/en/business", "/index.php/en/business"],
        "www.slt.lk": ["/en/business", "/index.php/en/business"],
    }

    allowed_prefixes = allowed_prefixes_by_domain.get(domain, ["/"])

    return any(path.startswith(prefix) for prefix in allowed_prefixes)


async def _run_ingestion_task(coro, *, label: str):
    """
    Wrap a long-running ingestion coroutine so its result/errors are captured
    into ingestion_status instead of bubbling out of the request.
    """
    try:
        logger.info("%s ingestion started", label)

        result = await coro

        if not isinstance(result, dict):
            result = {
                "status": "success",
                "message": str(result),
            }

        logger.info("%s ingestion finished: %s", label, result)

    except Exception as exc:
        logger.exception("%s ingestion failed", label)
        result = {
            "status": "error",
            "message": f"{type(exc).__name__}: {exc}",
        }

    ingestion_status.finish(result)


async def _crawl_and_ingest_urls(
    *,
    seed_urls: list[str],
    collection_name: str,
    crawl: bool,
    max_pages: int,
    max_depth: int,
):
    """
    Crawl URLs and ingest each visited page using the existing IngestionService.

    This reuses:
        ingestion_service.ingest_website(url, collection_name)

    So we do not need to manually implement embeddings or Qdrant insertion here.
    """
    visited: set[str] = set()
    queued: set[str] = set()
    queue = deque()

    normalized_seed_urls = [_normalize_url(url) for url in seed_urls]
    seed_domains = {urlparse(url).netloc for url in normalized_seed_urls}

    for url in normalized_seed_urls:
        queue.append((url, 0))
        queued.add(url)

    results = []
    skipped = []

    while queue and len(visited) < max_pages:
        current_url, depth = queue.popleft()

        if current_url in visited:
            continue

        visited.add(current_url)

        logger.info(
            "[CRAWL_VISIT] collection=%s url=%s depth=%s",
            collection_name,
            current_url,
            depth,
        )

        try:
            result = await ingestion_service.ingest_website(
                current_url,
                collection_name,
            )

            results.append(
                {
                    "url": current_url,
                    "depth": depth,
                    "result": result,
                }
            )

            logger.info(
                "[CRAWL_INGEST_SUCCESS] collection=%s url=%s result=%s",
                collection_name,
                current_url,
                result,
            )

        except Exception as exc:
            logger.exception(
                "[CRAWL_INGEST_FAIL] url=%s error=%s",
                current_url,
                exc,
            )

            skipped.append(
                {
                    "url": current_url,
                    "depth": depth,
                    "error": str(exc),
                }
            )

            continue

        if not crawl:
            continue

        if depth >= max_depth:
            continue

        try:
            html = await asyncio.to_thread(_fetch_html_sync, current_url)
            extracted_links = _extract_links(html, current_url)

            logger.info(
                "[CRAWL_EXTRACT] url=%s links=%s",
                current_url,
                len(extracted_links),
            )

            for next_url in extracted_links:
                if not _is_allowed_url(next_url, seed_domains):
                    continue

                if next_url in visited or next_url in queued:
                    continue

                if len(visited) + len(queue) >= max_pages:
                    break

                queue.append((next_url, depth + 1))
                queued.add(next_url)

                logger.info(
                    "[CRAWL_QUEUE] parent=%s child=%s depth=%s",
                    current_url,
                    next_url,
                    depth + 1,
                )

        except Exception as exc:
            logger.warning(
                "[CRAWL_LINK_EXTRACT_FAIL] url=%s error=%s",
                current_url,
                exc,
            )

            skipped.append(
                {
                    "url": current_url,
                    "depth": depth,
                    "error": f"Link extraction failed: {str(exc)}",
                }
            )

    return {
        "status": "success",
        "message": (
            f"Ingestion completed for collection '{collection_name}'. "
            f"Visited {len(visited)} page(s)."
        ),
        "collection_name": collection_name,
        "visited_count": len(visited),
        "visited": list(visited),
        "results": results,
        "skipped": skipped,
    }


@router.post("/ingest-url")
async def ingest_url(request: UrlIngestRequest):
    """
    Ingest content from URL(s) and store embeddings in Qdrant.
    Runs in the background; poll /ingestion-status for completion.

    Old request format:
    {
      "url": "https://lifestore.lk/products?categories=All&products",
      "agent_name": "ask_lifestore"
    }

    New crawler request format:
    {
      "urls": ["https://lifestore.lk/products?categories=All&products"],
      "collection_name": "ask_lifestore",
      "crawl": true,
      "max_pages": 200,
      "max_depth": 3
    }
    """
    current = ingestion_status.get_status()

    if current.get("active"):
        raise HTTPException(
            status_code=409,
            detail="Another ingestion job is already running.",
        )

    seed_urls = request.urls or ([request.url] if request.url else [])
    collection_name = request.collection_name or request.agent_name

    if not seed_urls:
        raise HTTPException(
            status_code=400,
            detail="Either 'url' or 'urls' is required.",
        )

    if not collection_name:
        raise HTTPException(
            status_code=400,
            detail="Either 'agent_name' or 'collection_name' is required.",
        )

    max_pages = int(request.max_pages or 200)
    max_depth = int(request.max_depth or 3)

    ingestion_status.start(agent_name=collection_name, source="url")

    asyncio.create_task(
        _run_ingestion_task(
            _crawl_and_ingest_urls(
                seed_urls=seed_urls,
                collection_name=collection_name,
                crawl=request.crawl,
                max_pages=max_pages,
                max_depth=max_depth,
            ),
            label=(
                f"URL collection='{collection_name}' "
                f"urls={seed_urls} crawl={request.crawl}"
            ),
        )
    )

    return {
        "status": "started",
        "agent_name": collection_name,
        "collection_name": collection_name,
        "source": "url",
        "crawl": request.crawl,
        "max_pages": max_pages,
        "max_depth": max_depth,
    }


@router.post("/ingest-onedrive")
async def process_onedrive_ingestion_api(request: OneDriveIngestRequest):
    """
    Process OneDrive Ingestion API.

    Ingest PDFs, Word docs, PowerPoint and Excel files from a OneDrive folder
    using the Graph API. Runs in the background; poll /ingestion-status for completion.
    """
    current = ingestion_status.get_status()

    if current.get("active"):
        raise HTTPException(
            status_code=409,
            detail="Another ingestion job is already running.",
        )

    ingestion_status.start(agent_name=request.agent_name, source="onedrive")

    asyncio.create_task(
        _run_ingestion_task(
            ingestion_service.process_onedrive_ingestion(
                folder_id=request.folder_id,
                access_token=request.token,
                agent_name=request.agent_name,
                force=request.force,
            ),
            label=f"OneDrive agent='{request.agent_name}' folder='{request.folder_id}'",
        )
    )

    return {
        "status": "started",
        "agent_name": request.agent_name,
        "source": "onedrive",
    }


@router.get("/ingestion-status")
async def get_ingestion_status():
    """
    Current or last ingestion state.
    Used by admin panel to survive page refresh.
    """
    return ingestion_status.get_status()


@router.post("/test-leave-balance")
async def test_leave_balance(request: TestLeaveBalanceRequest):
    """
    Test the external SLTMobitel ERP leave balance API directly.
    """
    sid = request.sid

    if not sid:
        raise HTTPException(
            status_code=400,
            detail="Service ID (sid) is required.",
        )

    try:
        response = requests.post(
            LEAVE_BALANCE_API_URL,
            json={"sid": sid},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.error("Leave API returned status %s", response.status_code)

            return {
                "status": "error",
                "message": f"Leave API returned status {response.status_code}",
                "detail": response.text,
            }

        data = response.json()

        return {
            "status": "success",
            "data": data,
        }

    except requests.Timeout:
        logger.error("Leave API request timed out")

        raise HTTPException(
            status_code=504,
            detail="The leave balance request timed out.",
        )

    except Exception as exc:
        logger.error("Error fetching leave balance: %s", exc)

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )