import os
import shutil
import tempfile
import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from core.llm import get_embedding_model
from langchain_text_splitters import HTMLHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_unstructured import UnstructuredLoader
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
import pytesseract

# Explicitly set Tesseract path for Windows environments
import sys
if sys.platform == "win32":
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

from qdrant_client import QdrantClient, models

from core.config import settings

log = logging.getLogger(__name__)

router = APIRouter()

class IngestionService:
    def __init__(self):
        # 1. Initialize the Embedding Model from Factory
        self.embeddings = get_embedding_model()

        # 2. Initialize Qdrant Client
        self.client = QdrantClient(url=settings.QDRANT_URL)

        # 3. Secondary splitter for chunks that are still too large
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1600,
            chunk_overlap=300,
        )

    async def _ensure_collection_exists(self, collection_name: str):
        """Manually checks if a collection exists. If not, creates it safely."""
        try:
            self.client.get_collection(collection_name)
        except Exception:
            print(f"Collection '{collection_name}' not found. Creating it manually...")
            # Use the dimension size from settings (default 3072 for Gemini)
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=settings.EMBEDDING_DIMENSIONS,
                    distance=models.Distance.COSINE,
                ),
            )

    async def ingest_website(self, url: str, agent_name: str):
        """Ingest a web page using HTML-header-aware splitting.

        First splits on HTML headings (h1-h4) so each chunk inherits its
        section hierarchy as metadata.  Then applies the recursive splitter
        to break oversized sections down to the target chunk size.
        """
        # Load raw HTML
        loader = WebBaseLoader(url)
        documents = loader.load()

        # Split by HTML headings — each chunk gets header metadata
        html_splitter = HTMLHeaderTextSplitter(
            headers_to_split_on=[
                ("h1", "Header 1"),
                ("h2", "Header 2"),
                ("h3", "Header 3"),
                ("h4", "Header 4"),
            ],
        )

        header_docs = []
        for doc in documents:
            header_docs.extend(html_splitter.split_text(doc.page_content))

        # Secondary split for sections that exceed the target chunk size
        docs = self.recursive_splitter.split_documents(header_docs)

        # Prepend header breadcrumb into chunk content for retrieval context
        for doc in docs:
            headers = [
                doc.metadata[h]
                for h in ("Header 1", "Header 2", "Header 3", "Header 4")
                if doc.metadata.get(h)
            ]
            if headers:
                breadcrumb = " > ".join(headers)
                if not doc.page_content.startswith(breadcrumb):
                    doc.page_content = f"[Section: {breadcrumb}]\n{doc.page_content}"
            doc.metadata["link"] = url

        # Define Collection Name
        collection_name = f"{agent_name}_docs"

        # Create collection manually first
        await self._ensure_collection_exists(collection_name)

        # Upsert to Qdrant
        vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
        )
        vector_store.add_documents(docs)

        return {
            "status": "success",
            "message": f"Ingested {len(docs)} chunks from {url} for agent {agent_name}",
        }

        # Cleaned up _authenticate_graph method

    def _load_and_chunk_file(self, file_path: Path) -> list[Document]:
        """Use unstructured's native semantic chunking by headers and sections."""
        ext = file_path.suffix.lower()
        if ext == ".xlsx":
            log.info(f"   📊 Excel file detected ({file_path.name}).")

        loader = UnstructuredLoader(
            file_path=str(file_path),
            chunking_strategy="by_title",
            max_characters=2500,
            combine_text_under_n_chars=500,
            strategy="hi_res",
            languages=["eng", "sin"],
        )
        docs = loader.load()

        # Re-split any chunks that are still too large after semantic
        # chunking.  Oversized chunks dilute embedding precision because
        # the vector becomes an average of multiple concepts.  The
        # recursive splitter preserves metadata and adds overlap so
        # answers that straddle a boundary are not lost.
        final_docs = []
        for doc in docs:
            if len(doc.page_content) > self.recursive_splitter._chunk_size:
                sub_chunks = self.recursive_splitter.split_documents([doc])
                final_docs.extend(sub_chunks)
            else:
                final_docs.append(doc)

        return final_docs

    def _file_already_ingested(self, collection_name: str, onedrive_id: str, last_modified: str) -> bool:
        """Check if a file with this onedrive_id and lastModified timestamp
        already has vectors in the collection. Returns True if up-to-date."""
        try:
            result = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.onedrive_id",
                            match=models.MatchValue(value=onedrive_id),
                        )
                    ]
                ),
                limit=1,
            )
            points = result[0]
            if not points:
                return False
            stored_modified = points[0].payload.get("metadata", {}).get("last_modified", "")
            return stored_modified == last_modified
        except Exception:
            return False

    def _delete_file_vectors(self, collection_name: str, onedrive_id: str, file_name: str):
        """Delete all existing vectors for a given onedrive_id."""
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="metadata.onedrive_id",
                                match=models.MatchValue(value=onedrive_id),
                            )
                        ]
                    )
                ),
            )
            log.info(f"Deleted old vectors for {file_name} (onedrive_id={onedrive_id})")
        except Exception as e:
            log.warning(f"Could not delete old vectors for {file_name}: {e}")

    async def process_onedrive_ingestion(self, folder_id: str, access_token: str, agent_name: str, force: bool = False):
        """
        Ingest PDFs, Word docs, Powerpoint and Excel from a OneDrive folder 
        using a direct Graph API token.
        Downloads files -> Semantically chunks them -> Upserts to Qdrant.
        """
        import requests 
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter

        # Setup a Resilient Session
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        session.mount('http://', HTTPAdapter(max_retries=retries))

        # 1. List files in the folder
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_id}/children"
        
        try:
            resp = session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "message": f"Graph API Error: {resp.status_code} {resp.text}",
                }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to connect to Microsoft Graph API: {e}",
            }

        items = resp.json().get("value", [])
        ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.eml')
        matching_items = [
            item for item in items 
            if item.get("file") and item.get("name", "").lower().endswith(ALLOWED_EXTENSIONS)
        ]

        if not matching_items:
            return {
                "status": "warning",
                "message": f"No supported files found in folder {folder_id}",
            }

        # 2. Download and process each file
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            total_chunks = 0
            processed_files = []

            # Define Collection Name
            collection_name = f"{agent_name}_docs"
            await self._ensure_collection_exists(collection_name)
            
            # Initialize Vector Store once
            vector_store = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
                embedding=self.embeddings,
            )

            skipped_files = []

            for item in matching_items:
                file_name = item["name"]
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    continue

                onedrive_id = item.get("id", "unknown")
                last_modified = item.get("lastModifiedDateTime", "")

                # Skip files that are already ingested and unchanged,
                # unless force re-ingestion is requested.
                if not force and self._file_already_ingested(
                    collection_name, onedrive_id, last_modified
                ):
                    log.info(f"Skipping unchanged file: {file_name}")
                    skipped_files.append(file_name)
                    continue

                # Delete old vectors before re-ingesting modified files
                self._delete_file_vectors(collection_name, onedrive_id, file_name)

                dest_path = temp_dir / file_name

                try:
                    # Download
                    print(f"Downloading {file_name}...")
                    file_resp = session.get(download_url, timeout=30)
                    if file_resp.status_code == 200:
                        with open(dest_path, "wb") as f:
                            f.write(file_resp.content)

                        # Chunk using semantic logic
                        chunks = self._load_and_chunk_file(dest_path)
                        if chunks:
                            for doc in chunks:
                                doc.metadata["source"] = file_name
                                doc.metadata["link"] = item.get("webUrl", "#")
                                doc.metadata["onedrive_id"] = onedrive_id
                                doc.metadata["source_folder"] = folder_id
                                doc.metadata["last_modified"] = last_modified

                            vector_store.add_documents(chunks)
                            total_chunks += len(chunks)
                            processed_files.append(file_name)
                    else:
                        print(f"Failed to download {file_name}: Status Code {file_resp.status_code}")
                except Exception as e:
                    print(f"Failed to process {file_name}: {e}")
                    continue

            return {
                "status": "success",
                "message": (
                    f"Ingested {total_chunks} chunks from {len(processed_files)} files. "
                    f"Skipped {len(skipped_files)} unchanged files."
                ),
                "files": processed_files,
                "skipped": skipped_files,
            }


ingestion_service = IngestionService()