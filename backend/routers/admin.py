from fastapi import APIRouter, UploadFile, File, Form, Body
from pydantic import BaseModel
from services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
ingestion_service = IngestionService()

class UrlIngestRequest(BaseModel):
    url: str
    agent_name: str

class OneDriveIngestRequest(BaseModel):
    folder_id: str
    token: str
    agent_name: str

@router.post("/ingest-url")
async def ingest_url(request: UrlIngestRequest):
    """
    Ingest content from a URL and store embeddings in Qdrant.
    """
    return await ingestion_service.ingest_website(request.url, request.agent_name)

@router.post("/ingest-onedrive")
async def process_onedrive_ingestion_api(request: OneDriveIngestRequest):
    """
    Process Onedrive Ingestion Api - Ingest PDFs, Word docs, Powerpoint and Excel 
    from a OneDrive folder using the Graph API.
    """
    return await ingestion_service.process_onedrive_ingestion(
        folder_id=request.folder_id,
        access_token=request.token,
        agent_name=request.agent_name
    )

