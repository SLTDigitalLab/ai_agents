"""
Parallel ingestion service for the internal SLM demo (Ask HR SLM).

Re-uses the file-loading / chunking helpers from the main IngestionService
but writes to a separate Qdrant collection sized for the Ollama embedding
model (768d for nomic-embed-text), so the existing 3072d collections are
untouched.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient, models

from core.config import settings
from core.llm_slm import get_slm_embedding_model
from services.ingestion import ingestion_service as _shared_ingestion_service

log = logging.getLogger(__name__)


class SLMIngestionService:
    """OneDrive → Qdrant ingestion using Ollama embeddings (768d collection)."""

    def __init__(self):
        self.embeddings = get_slm_embedding_model()
        self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")
        self.client = QdrantClient(url=settings.QDRANT_URL)
        # Re-use chunking + file-loading helpers from the existing service.
        self._helpers = _shared_ingestion_service

    def _ensure_collection_exists(self, collection_name: str):
        try:
            self.client.get_collection(collection_name)
        except Exception:
            log.info(f"Creating SLM collection '{collection_name}' "
                     f"(dim={settings.SLM_EMBEDDING_DIMENSIONS})")
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=settings.SLM_EMBEDDING_DIMENSIONS,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False),
                    ),
                },
            )

    async def process_onedrive_ingestion(
        self, folder_id: str, access_token: str, agent_name: str, force: bool = False
    ):
        """Async wrapper — offloads the blocking ingestion to a worker thread
        so the event loop stays free to serve chat during ingestion."""
        return await asyncio.to_thread(
            self._process_onedrive_sync, folder_id, access_token, agent_name, force
        )

    def _process_onedrive_sync(
        self, folder_id: str, access_token: str, agent_name: str, force: bool = False
    ):
        import requests
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter

        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        session.mount("http://", HTTPAdapter(max_retries=retries))

        headers = {"Authorization": f"Bearer {access_token}"}
        url = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_id}/children"
            f"?$top=200"
        )

        items = []
        try:
            while url:
                resp = session.get(url, headers=headers, timeout=30)
                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "message": f"Graph API Error: {resp.status_code} {resp.text}",
                    }
                payload = resp.json()
                items.extend(payload.get("value", []))
                url = payload.get("@odata.nextLink")
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to connect to Microsoft Graph API: {e}",
            }

        log.info(f"[SLM] Graph returned {len(items)} items for folder {folder_id}")
        ALLOWED = (".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".eml")
        matching = [
            it for it in items
            if it.get("file") and it.get("name", "").lower().endswith(ALLOWED)
        ]
        if not matching:
            return {
                "status": "warning",
                "message": f"No supported files found in folder {folder_id}",
            }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            total_chunks = 0
            processed_files, skipped_files, failed_files = [], [], []

            collection_name = f"{agent_name}_docs"
            self._ensure_collection_exists(collection_name)

            vector_store = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
                embedding=self.embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
                vector_name="dense",
                sparse_vector_name="sparse",
            )

            for item in matching:
                file_name = item["name"]
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    failed_files.append({"file": file_name, "reason": "no download URL"})
                    continue

                onedrive_id = item.get("id", "unknown")
                last_modified = item.get("lastModifiedDateTime", "")

                if not force and self._helpers._file_already_ingested(
                    collection_name, onedrive_id, last_modified
                ):
                    skipped_files.append(file_name)
                    continue

                dest_path = temp_dir / file_name
                try:
                    log.info(f"[SLM] Downloading {file_name}...")
                    file_resp = session.get(download_url, timeout=120)
                    if file_resp.status_code != 200:
                        failed_files.append(
                            {"file": file_name, "reason": f"HTTP {file_resp.status_code}"}
                        )
                        continue
                    with open(dest_path, "wb") as f:
                        f.write(file_resp.content)

                    chunks = self._helpers._load_and_chunk_file(dest_path)
                    if not chunks:
                        failed_files.append(
                            {"file": file_name, "reason": "no extractable text"}
                        )
                        continue

                    for doc in chunks:
                        doc.metadata["source"] = file_name
                        doc.metadata["link"] = item.get("webUrl", "#")
                        doc.metadata["onedrive_id"] = onedrive_id
                        doc.metadata["source_folder"] = folder_id
                        doc.metadata["last_modified"] = last_modified

                    self._helpers._delete_file_vectors(
                        collection_name, onedrive_id, file_name
                    )
                    vector_store.add_documents(chunks)
                    total_chunks += len(chunks)
                    processed_files.append(file_name)
                except Exception as e:
                    log.exception(f"[SLM] Failed to process {file_name}: {e}")
                    failed_files.append({"file": file_name, "reason": str(e)})
                    continue

            return {
                "status": "success",
                "message": (
                    f"[SLM] Ingested {total_chunks} chunks from "
                    f"{len(processed_files)} files. "
                    f"Skipped {len(skipped_files)}. Failed {len(failed_files)}."
                ),
                "files": processed_files,
                "skipped": skipped_files,
                "failed": failed_files,
            }


slm_ingestion_service = SLMIngestionService()
