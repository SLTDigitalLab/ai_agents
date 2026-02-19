from fastapi import APIRouter, UploadFile, File, Form, Body
from pydantic import BaseModel
from services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
ingestion_service = IngestionService()

class UrlIngestRequest(BaseModel):
    url: str
    agent_name: str

@router.post("/ingest-file")
async def ingest_file(
    file: UploadFile = File(...),
    agent_name: str = Form(...)
):
    """
    Ingest a PDF file and store embeddings in Qdrant.
    """
    return await ingestion_service.ingest_file(file, agent_name)

@router.post("/ingest-url")
async def ingest_url(request: UrlIngestRequest):
    """
    Ingest content from a URL and store embeddings in Qdrant.
    """
    return await ingestion_service.ingest_website(request.url, request.agent_name)

class OneDriveIngestRequest(BaseModel):
    folder_id: str
    token: str
    agent_name: str

@router.post("/ingest-onedrive")
async def ingest_onedrive(request: OneDriveIngestRequest):
    """
    Ingest PDFs from a OneDrive folder using a Graph API access token.
    """
    return await ingestion_service.ingest_onedrive_folder(
        request.folder_id, request.token, request.agent_name
    )
