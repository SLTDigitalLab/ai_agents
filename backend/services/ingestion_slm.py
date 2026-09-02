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
    """
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
    """

    def _ensure_collection_exists(self, collection_name: str, force: bool = False):
        """Ensures collection exists with named vectors ('dense' and 'sparse'). 
        Recreates if force=True."""
        
        # 1. Drop existing collection if force=True
        if force and self.client.collection_exists(collection_name):
            log.info(f"Force re-index requested. Deleting existing collection '{collection_name}'...")
            self.client.delete_collection(collection_name)

        # 2. Create collection if it doesn't exist
        if not self.client.collection_exists(collection_name):
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
        
        # Log all items to diagnose what's in the folder
        unsupported_files = []
        for it in items:
            name = it.get("name", "unknown")
            ext = Path(name).suffix.lower()
            is_file = bool(it.get("file"))
            if is_file:
                if name.lower().endswith(ALLOWED):
                    log.info(f"  ✓ Supported: {name} ({ext})")
                else:
                    log.warning(f"  ✗ Unsupported: {name} ({ext})")
                    unsupported_files.append({"name": name, "ext": ext})
            else:
                log.info(f"  📁 Folder (will skip): {name}")
        
        matching = [
            it for it in items
            if it.get("file") and it.get("name", "").lower().endswith(ALLOWED)
        ]
        if not matching:
            msg = (
                f"No supported files found in folder {folder_id}. "
                f"Found {len(unsupported_files)} unsupported files: "
                f"{', '.join(f.get('name') for f in unsupported_files[:5])}"
            )
            log.warning(f"[SLM] {msg}")
            return {
                "status": "warning",
                "message": msg,
            }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            total_chunks = 0
            processed_files, skipped_files, failed_files = [], [], []

            collection_name = f"{agent_name}_docs"
            self._ensure_collection_exists(collection_name, force=force)
        
        
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
                    log.warning(f"[SLM] ✗ {file_name}: no download URL")
                    continue

                onedrive_id = item.get("id", "unknown")
                last_modified = item.get("lastModifiedDateTime", "")
                file_ext = Path(file_name).suffix.lower()

                if not force and self._helpers._file_already_ingested(
                    collection_name, onedrive_id, last_modified
                ):
                    skipped_files.append(file_name)
                    log.info(f"[SLM] ⏭  {file_name}: already ingested, skipping")
                    continue

                dest_path = temp_dir / file_name
                try:
                    log.info(f"[SLM] 📥 Downloading {file_name} ({file_ext})...")
                    file_resp = session.get(download_url, timeout=120)
                    if file_resp.status_code != 200:
                        failed_files.append(
                            {"file": file_name, "reason": f"HTTP {file_resp.status_code}"}
                        )
                        log.error(f"[SLM] ✗ {file_name}: HTTP {file_resp.status_code}")
                        continue
                    
                    file_size_bytes = len(file_resp.content)
                    with open(dest_path, "wb") as f:
                        f.write(file_resp.content)
                    
                    log.info(f"[SLM] ✓ Downloaded {file_name} ({file_size_bytes} bytes)")

                    chunks = self._helpers._load_and_chunk_file(dest_path)
                    num_chunks = len(chunks)
                    total_chars = sum(len(c.page_content) for c in chunks) if chunks else 0
                    
                    if not chunks:
                        failed_files.append(
                            {"file": file_name, "reason": "no extractable text (parser returned 0 chunks)"}
                        )
                        log.error(f"[SLM] ✗ {file_name}: parser extracted 0 chunks from {file_size_bytes} bytes")
                        continue
                    
                    log.info(f"[SLM] ✓ {file_name}: extracted {num_chunks} chunks ({total_chars} total chars)")

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
                    log.info(f"[SLM] ✅ {file_name}: inserted {num_chunks} chunks into Qdrant")
                except Exception as e:
                    log.exception(f"[SLM] ✗ {file_name}: {type(e).__name__}: {e}")
                    failed_files.append({"file": file_name, "reason": f"{type(e).__name__}: {str(e)[:100]}"})
                    continue

            log.info(f"[SLM] === Ingestion Summary ===")
            log.info(f"[SLM] Processed: {len(processed_files)} files, {total_chunks} chunks")
            log.info(f"[SLM] Skipped: {len(skipped_files)}")
            log.info(f"[SLM] Failed: {len(failed_files)}")
            if failed_files:
                log.info(f"[SLM] Failed file details:")
                for fail in failed_files:
                    log.info(f"  - {fail.get('file')}: {fail.get('reason')}")
            
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
