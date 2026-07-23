from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import asyncio
import json
import logging
import requests

from collections import deque
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

from services.ingestion import IngestionService
from services.ingestion_slm import slm_ingestion_service
from services import ingestion_status
from domain.tools.api_tools import LEAVE_BALANCE_API_URL
from core.config import settings


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
ingestion_service = IngestionService()


try:
    ADMIN_AGENT_MAP: dict[str, list[str]] = {
        email.lower(): agents
        for email, agents in json.loads(settings.ADMIN_AGENT_MAP or "{}").items()
    }
except json.JSONDecodeError as e:
    logger.error(f"ADMIN_AGENT_MAP is not valid JSON: {e}")
    ADMIN_AGENT_MAP = {}


def require_agent_access(user_email: str | None, agent_name: str) -> None:
    """Reject if the caller's email is not authorised for this agent.

    A user with ["*"] is treated as super-admin (access to every agent).
    """
    if not user_email:
        raise HTTPException(status_code=401, detail="Missing user_email.")
    allowed = ADMIN_AGENT_MAP.get(user_email.lower(), [])
    if "*" in allowed or agent_name in allowed:
        return
    raise HTTPException(
        status_code=403,
        detail=f"User '{user_email}' is not authorised to ingest into agent '{agent_name}'.",
    )


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

    user_email: str | None = None

class OneDriveIngestRequest(BaseModel):
    folder_id: str
    token: str
    agent_name: str
    force: bool = False
    user_email: str | None = None


class SharePointIngestRequest(BaseModel):
    site_url: str
    folder_path: str
    token: str
    agent_name: str
    force: bool = False   

class KBListRequest(BaseModel):
    agent_name: str
    user_email: str | None = None


class KBDeleteDocumentRequest(BaseModel):
    agent_name: str
    document_id: str | None = None
    source: str | None = None
    user_email: str | None = None


class KBDeleteAgentRequest(BaseModel):
    agent_name: str
    user_email: str | None = None    

class TestLeaveBalanceRequest(BaseModel):
    sid: str

class KBListChunksRequest(BaseModel):
    agent_name: str
    document_id: str | None = None
    source: str | None = None
    user_email: str | None = None

class KBDeleteChunkRequest(BaseModel):
    agent_name: str
    point_id: str
    user_email: str | None = None

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
    require_agent_access(request.user_email, request.agent_name)

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
    require_agent_access(request.user_email, request.agent_name)

    current = ingestion_status.get_status()

    if current.get("active"):
        raise HTTPException(
            status_code=409,
            detail="Another ingestion job is already running.",
        )

    ingestion_status.start(agent_name=request.agent_name, source="onedrive")

    # Route askhrslm to the SLM ingestion service (Ollama embeddings, 768d
    # collection). All other agents use the default OpenAI/Gemini pipeline.
    if request.agent_name == "askhrslm":
        coro = slm_ingestion_service.process_onedrive_ingestion(
            folder_id=request.folder_id,
            access_token=request.token,
            agent_name=request.agent_name,
            force=request.force,
        )
    else:
        coro = ingestion_service.process_onedrive_ingestion(
            folder_id=request.folder_id,
            access_token=request.token,
            agent_name=request.agent_name,
            force=request.force,
        )

    asyncio.create_task(
        _run_ingestion_task(
            coro,
            label=f"OneDrive agent='{request.agent_name}' folder='{request.folder_id}'",
        )
    )

    return {
        "status": "started",
        "agent_name": request.agent_name,
        "source": "onedrive",
    }


@router.post("/ingest-sharepoint")
async def process_sharepoint_ingestion_api(request: SharePointIngestRequest):
    """
    Process SharePoint Ingestion API - Ingest PDFs, Word docs, PowerPoint,
    Excel, images, and EML files from a SharePoint document library folder
    using Microsoft Graph.

    Runs in the background; poll /ingestion-status for completion.
    """
    current = ingestion_status.get_status()
    if current.get("active"):
        raise HTTPException(status_code=409, detail="Another ingestion job is already running.")

    ingestion_status.start(agent_name=request.agent_name, source="sharepoint")

    asyncio.create_task(
        _run_ingestion_task(
            ingestion_service.process_sharepoint_ingestion(
                site_url=request.site_url,
                folder_path=request.folder_path,
                access_token=request.token,
                agent_name=request.agent_name,
                force=request.force,
            ),
            label=(
                f"SharePoint agent='{request.agent_name}' "
                f"site_url='{request.site_url}' folder_path='{request.folder_path}'"
            ),
        )
    )

    return {
        "status": "started",
        "agent_name": request.agent_name,
        "source": "sharepoint",
    }

@router.get("/ingestion-status")
async def get_ingestion_status():
    """
    Current or last ingestion state.
    Used by admin panel to survive page refresh.
    """
    return ingestion_status.get_status()

@router.post("/kb-documents")
async def list_kb_documents(request: KBListRequest):
    """
    List documents currently stored in the selected agent KB.
    """
    require_agent_access(request.user_email, request.agent_name)

    result = await asyncio.to_thread(
        ingestion_service.list_kb_documents,
        request.agent_name,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result


@router.post("/delete-kb-document")
async def delete_kb_document(request: KBDeleteDocumentRequest):
    """
    Delete one document from selected agent KB.
    """
    require_agent_access(request.user_email, request.agent_name)

    result = await asyncio.to_thread(
        ingestion_service.delete_kb_document,
        request.agent_name,
        request.document_id,
        request.source,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result


@router.post("/delete-agent-kb")
async def delete_agent_kb(request: KBDeleteAgentRequest):
    """
    Delete full KB collection for selected agent.
    """
    require_agent_access(request.user_email, request.agent_name)

    result = await asyncio.to_thread(
        ingestion_service.delete_agent_kb,
        request.agent_name,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result

@router.post("/kb-document-chunks")
async def list_kb_document_chunks(request: KBListChunksRequest):
    """
    List all chunks for one document in selected agent KB.
    """
    require_agent_access(request.user_email, request.agent_name)

    result = await asyncio.to_thread(
        ingestion_service.list_kb_document_chunks,
        request.agent_name,
        request.document_id,
        request.source,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result


@router.post("/delete-kb-chunk")
async def delete_kb_chunk(request: KBDeleteChunkRequest):
    """
    Delete one selected chunk from selected agent KB.
    """
    require_agent_access(request.user_email, request.agent_name)

    result = await asyncio.to_thread(
        ingestion_service.delete_kb_chunk,
        request.agent_name,
        request.point_id,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result

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