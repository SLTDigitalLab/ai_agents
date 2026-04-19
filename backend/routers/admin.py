from fastapi import APIRouter, UploadFile, File, Form, Body, HTTPException
from pydantic import BaseModel
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

@router.post("/ingest-url")
async def ingest_url(request: UrlIngestRequest):
    """
    Ingest content from a URL and store embeddings in Qdrant.
    """
    ingestion_status.start(agent_name=request.agent_name, source="url")
    result = None
    try:
        result = await ingestion_service.ingest_website(request.url, request.agent_name)
    except Exception as exc:
        logger.exception(f"URL ingestion failed for agent='{request.agent_name}' url='{request.url}'")
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        raise
    finally:
        ingestion_status.finish(result if isinstance(result, dict) else {"status": "error", "message": "Unknown error"})
    return result

@router.post("/ingest-onedrive")
async def process_onedrive_ingestion_api(request: OneDriveIngestRequest):
    """
    Process Onedrive Ingestion Api - Ingest PDFs, Word docs, Powerpoint and Excel
    from a OneDrive folder using the Graph API.
    """
    ingestion_status.start(agent_name=request.agent_name, source="onedrive")
    result = None
    try:
        result = await ingestion_service.process_onedrive_ingestion(
            folder_id=request.folder_id,
            access_token=request.token,
            agent_name=request.agent_name,
            force=request.force,
        )
    except Exception as exc:
        logger.exception(f"OneDrive ingestion failed for agent='{request.agent_name}' folder='{request.folder_id}'")
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        raise
    finally:
        ingestion_status.finish(result if isinstance(result, dict) else {"status": "error", "message": "Unknown error"})
    return result

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

