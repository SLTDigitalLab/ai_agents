"""
Document proxy router — serves OneDrive files through the backend
so employees can view source documents without a separate Microsoft login.

Uses the Azure AD client credentials flow to obtain an app-level token,
then fetches a fresh download URL from the Graph API and redirects the
user's browser to it.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from core.config import settings

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])
logger = logging.getLogger(__name__)

# Microsoft OAuth2 token endpoint
TOKEN_URL = f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}/oauth2/v2.0/token"


async def _get_app_token() -> str:
    """Obtain an app-level access token using the client credentials flow."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.MS_CLIENT_ID,
                "client_secret": settings.MS_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=15,
        )
    if resp.status_code != 200:
        logger.error(f"Failed to obtain app token: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=502, detail="Could not authenticate with Microsoft")
    return resp.json()["access_token"]


@router.get("/view")
async def view_document(
    drive_id: str = Query(..., description="OneDrive drive ID"),
    item_id: str = Query(..., description="OneDrive item ID"),
):
    """
    Fetch a fresh, pre-authenticated download URL for a OneDrive file
    and redirect the user's browser to it. Opens in a new tab as a
    direct file download/preview — no Microsoft login required.
    """
    if not all([settings.MS_CLIENT_ID, settings.MS_CLIENT_SECRET, settings.MS_TENANT_ID]):
        raise HTTPException(
            status_code=500,
            detail="Microsoft credentials are not configured on the server.",
        )

    token = await _get_app_token()

    # Use the drive-level path so app credentials can access any user's files
    graph_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            graph_url,
            headers={"Authorization": f"Bearer {token}"},
            params={"select": "@microsoft.graph.downloadUrl,name,webUrl"},
            timeout=15,
        )

    if resp.status_code != 200:
        logger.error(f"Graph API error: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=404, detail="Document not found or not accessible")

    data = resp.json()
    download_url = data.get("@microsoft.graph.downloadUrl")

    if not download_url:
        raise HTTPException(status_code=404, detail="No download URL available for this document")

    # Redirect the user's browser to the pre-authenticated download URL
    return RedirectResponse(url=download_url)
