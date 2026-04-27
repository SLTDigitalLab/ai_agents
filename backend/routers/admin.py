from fastapi import APIRouter, UploadFile, File, Form, Body, HTTPException
from pydantic import BaseModel
import asyncio
import requests
import logging
from services.ingestion import IngestionService
from services import ingestion_status
from domain.tools.api_tools import LEAVE_BALANCE_API_URL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
ingestion_service = IngestionService()

class UrlIngestRequest(BaseModel):
    url: str
    agent_name: str

class OneDriveIngestRequest(BaseModel):
    folder_id: str
    token: str
    agent_name: str
    force: bool = False

class TestLeaveBalanceRequest(BaseModel):
    sid: str

async def _run_ingestion_task(coro, *, label: str):
    """Wrap a long-running ingestion coroutine so its result/errors are
    captured into ingestion_status instead of bubbling out of the request."""
    try:
        result = await coro
        if not isinstance(result, dict):
            result = {"status": "error", "message": "Unknown error"}
    except Exception as exc:
        logger.exception(f"{label} ingestion failed")
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
    ingestion_status.finish(result)


@router.post("/ingest-url")
async def ingest_url(request: UrlIngestRequest):
    """
    Ingest content from a URL and store embeddings in Qdrant.
    Runs in the background; poll /ingestion-status for completion.
    """
    current = ingestion_status.get_status()
    if current.get("active"):
        raise HTTPException(status_code=409, detail="Another ingestion job is already running.")

    ingestion_status.start(agent_name=request.agent_name, source="url")
    asyncio.create_task(
        _run_ingestion_task(
            ingestion_service.ingest_website(request.url, request.agent_name),
            label=f"URL agent='{request.agent_name}' url='{request.url}'",
        )
    )
    return {"status": "started", "agent_name": request.agent_name, "source": "url"}


@router.post("/ingest-onedrive")
async def process_onedrive_ingestion_api(request: OneDriveIngestRequest):
    """
    Process Onedrive Ingestion Api - Ingest PDFs, Word docs, Powerpoint and Excel
    from a OneDrive folder using the Graph API.
    Runs in the background; poll /ingestion-status for completion.
    """
    current = ingestion_status.get_status()
    if current.get("active"):
        raise HTTPException(status_code=409, detail="Another ingestion job is already running.")

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
    return {"status": "started", "agent_name": request.agent_name, "source": "onedrive"}

@router.get("/ingestion-status")
async def get_ingestion_status():
    """Current or last ingestion state — used by admin panel to survive page refresh."""
    return ingestion_status.get_status()

@router.post("/test-leave-balance")
async def test_leave_balance(request: TestLeaveBalanceRequest):
    """
    Test the external SLTMobitel ERP leave balance API directly.
    """
    sid = request.sid
    if not sid:
        raise HTTPException(status_code=400, detail="Service ID (sid) is required.")

    try:
        response = requests.post(
            LEAVE_BALANCE_API_URL,
            json={"sid": sid},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.error(f"Leave API returned status {response.status_code}")
            return {
                "status": "error",
                "message": f"Leave API returned status {response.status_code}",
                "detail": response.text
            }

        data = response.json()
        return {
            "status": "success",
            "data": data
        }

    except requests.Timeout:
        logger.error("Leave API request timed out")
        raise HTTPException(status_code=504, detail="The leave balance request timed out.")
    except Exception as exc:
        logger.error(f"Error fetching leave balance: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

