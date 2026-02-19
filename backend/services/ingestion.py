"""
Ingestion service - converts PDFs and web pages into semantically chunked
vectors stored in Qdrant.

Uses **pymupdf4llm** to extract Markdown-formatted text from PDFs, then
splits by Markdown headers first (preserving section boundaries) and
applies a secondary recursive split for any oversized chunks.
"""

import os
import shutil

import pymupdf4llm
from fastapi import UploadFile
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient, models

from core.config import settings


class IngestionService:
    def __init__(self):
        # 1. Initialize the SOTA Gemini Embedding Model
        # using the new standard: gemini-embedding-001
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=settings.GOOGLE_API_KEY,
        )

        # 2. Initialize Qdrant Client
        self.client = QdrantClient(url=settings.QDRANT_URL)

        # 3. Markdown header hierarchy to split on
        self.md_headers_to_split = [
            ("#", "H1"),
            ("##", "H2"),
            ("###", "H3"),
        ]

        # 4. Secondary splitter for chunks that are still too large
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=200,
        )

    async def _ensure_collection_exists(self, collection_name: str):
        """Manually checks if a collection exists. If not, creates it safely."""
        try:
            self.client.get_collection(collection_name)
        except Exception:
            print(f"Collection '{collection_name}' not found. Creating it manually...")
            # gemini-embedding-001 outputs 3072 dimensions
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=3072,
                    distance=models.Distance.COSINE,
                ),
            )

    def _semantic_chunk_pdf(self, file_path: str) -> list[Document]:
        """
        Convert a PDF to Markdown (via pymupdf4llm), then split
        semantically by headers before falling back to recursive splitting.
        """
        # Step 1: Extract Markdown from PDF
        md_text = pymupdf4llm.to_markdown(file_path)

        print(f"\n{'='*40}")
        print(f"DEBUG: Extracted {len(md_text)} chars of Markdown from {os.path.basename(file_path)}")
        print(f"{'='*40}\n")

        # Step 2: Split by Markdown headers (preserves section boundaries)
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.md_headers_to_split,
            strip_headers=False,
        )
        header_chunks = header_splitter.split_text(md_text)

        # Step 3: Secondary split for any chunks still over the size limit
        final_docs = self.recursive_splitter.split_documents(header_chunks)

        print(f"DEBUG: Created {len(final_docs)} semantic chunks "
              f"(from {len(header_chunks)} header-based sections)")

        return final_docs

    async def ingest_file(self, file: UploadFile, agent_name: str):
        """Ingest a PDF using semantic Markdown chunking."""
        # Save uploaded file to a temporary file
        temp_dir = "temp_files"
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, file.filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            # Semantic chunking pipeline
            docs = self._semantic_chunk_pdf(file_path)

            # Define Collection Name
            collection_name = f"{agent_name}_docs"

            # Create collection manually first
            await self._ensure_collection_exists(collection_name)

            # Upsert to Qdrant
            vector_store = Qdrant(
                client=self.client,
                collection_name=collection_name,
                embeddings=self.embeddings,
            )
            vector_store.add_documents(docs)

            return {
                "status": "success",
                "message": f"Ingested {len(docs)} semantic chunks for agent {agent_name}",
            }

        finally:
            # Cleanup temp file
            if os.path.exists(file_path):
                os.remove(file_path)

    async def ingest_website(self, url: str, agent_name: str):
        """Ingest a web page using recursive text splitting."""
        # Load URL
        loader = WebBaseLoader(url)
        documents = loader.load()

        # Split text
        docs = self.recursive_splitter.split_documents(documents)

        # Define Collection Name
        collection_name = f"{agent_name}_docs"

        # Create collection manually first
        await self._ensure_collection_exists(collection_name)

        # Upsert to Qdrant
        vector_store = Qdrant(
            client=self.client,
            collection_name=collection_name,
            embeddings=self.embeddings,
        )
        vector_store.add_documents(docs)

        return {
            "status": "success",
            "message": f"Ingested {len(docs)} chunks from {url} for agent {agent_name}",
        }

    async def ingest_onedrive_folder(self, folder_id: str, access_token: str, agent_name: str):
        """
        Ingest PDFs from a OneDrive folder using a direct Graph API token.
        Downloads files -> Semantically chunks them -> Upserts to Qdrant.
        """
        import requests 

        # 1. List files in the folder
        # We try '/me/drive/items/{id}/children' assuming a delegated user token.
        # If it fails, we might need to know the Drive ID, but usually /me/drive covers it.
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_id}/children"
        
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"Graph API Error: {resp.status_code} {resp.text}",
            }

        items = resp.json().get("value", [])
        pdf_items = [
            item for item in items 
            if item.get("file") and item.get("name", "").lower().endswith(".pdf")
        ]

        if not pdf_items:
            return {
                "status": "warning",
                "message": f"No PDF files found in folder {folder_id}",
            }

        # 2. Download and process each file
        temp_dir = "temp_onedrive"
        os.makedirs(temp_dir, exist_ok=True)
        
        total_chunks = 0
        processed_files = []

        try:
            # Define Collection Name
            collection_name = f"{agent_name}_docs"
            await self._ensure_collection_exists(collection_name)
            
            # Initialize Vector Store once
            vector_store = Qdrant(
                client=self.client,
                collection_name=collection_name,
                embeddings=self.embeddings,
            )

            for item in pdf_items:
                file_name = item["name"]
                # Use @microsoft.graph.downloadUrl
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    continue

                file_path = os.path.join(temp_dir, file_name)
                
                # Download
                print(f"Downloading {file_name}...")
                file_resp = requests.get(download_url)
                if file_resp.status_code == 200:
                    with open(file_path, "wb") as f:
                        f.write(file_resp.content)
                    
                    # Chunk using semantic logic
                    docs = self._semantic_chunk_pdf(file_path)
                    if docs:
                        vector_store.add_documents(docs)
                        total_chunks += len(docs)
                        processed_files.append(file_name)
                    
                    # Clean up individual file
                    os.remove(file_path)

            return {
                "status": "success",
                "message": f"Ingested {total_chunks} chunks from {len(processed_files)} files in folder {folder_id}",
                "files": processed_files
            }

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)