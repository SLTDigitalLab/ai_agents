"""
ingest_onedrive.py
==================
ETL script that ingests PDF documents from Microsoft OneDrive into a
Qdrant vector database, enforcing strict data separation by mapping
each OneDrive folder to its own Qdrant collection.

Uses the same stack as ``ingestion.py``:
  - **pymupdf4llm** for PDF → Markdown extraction
  - **MarkdownHeaderTextSplitter** + **RecursiveCharacterTextSplitter**
    for semantic chunking
  - **GoogleGenerativeAIEmbeddings** (gemini-embedding-001, dim=3072)
  - **Qdrant** from langchain_community

Prerequisites
-------------
pip install O365 langchain langchain-community langchain-google-genai \
            langchain-text-splitters qdrant-client pymupdf pymupdf4llm \
            python-dotenv

Authentication
--------------
Uses the **Client Credentials** flow (Application permissions).
Your Microsoft Entra ID App must have ``Files.Read.All`` (Application)
permission granted with admin consent.
"""

import os
import shutil
import logging
from pathlib import Path

import pymupdf4llm
from dotenv import load_dotenv, find_dotenv
from O365 import Account
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_community.vectorstores import Qdrant
from qdrant_client import QdrantClient, models

# ──────────────────────────────────────────────────────────────────────
# 1.  LOAD CONFIGURATION
# ──────────────────────────────────────────────────────────────────────
load_dotenv(find_dotenv())  # reads from .env in the same directory (or parent dirs)

CLIENT_ID      = os.getenv("MS_CLIENT_ID")       # Azure App (client) ID
CLIENT_SECRET  = os.getenv("MS_CLIENT_SECRET")    # Azure App client secret
TENANT_ID      = os.getenv("MS_TENANT_ID")        # Azure AD tenant ID
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")      # Gemini API key
QDRANT_URL     = os.getenv("QDRANT_URL", "http://localhost:6333")

# Validate that all required credentials are set
_REQUIRED = {
    "MS_CLIENT_ID": CLIENT_ID,
    "MS_CLIENT_SECRET": CLIENT_SECRET,
    "MS_TENANT_ID": TENANT_ID,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
}
_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        "Set them in your .env file or export them in your shell."
    )

# ──────────────────────────────────────────────────────────────────────
# 2.  FOLDER  →  QDRANT COLLECTION MAPPING
# ──────────────────────────────────────────────────────────────────────
# Keys   = exact folder names as they appear in OneDrive (root level).
# Values = the Qdrant collection each folder's documents will go into.
#
# ★  Edit the keys below to match YOUR OneDrive folder names,
#    and the values to the Qdrant collection names you want.
# ──────────────────────────────────────────────────────────────────────
FOLDER_MAP = {
    "HR_collection":      "hr_docs",        # ← OneDrive folder → Qdrant collection
    "Finance_collection":  "finance_docs",   # ← add / remove entries as needed
}

EMBEDDING_DIMENSION = 3072

# ──────────────────────────────────────────────────────────────────────
# 3.  LOGGING
# ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("onedrive_ingest")

# ──────────────────────────────────────────────────────────────────────
# 4.  HELPER FUNCTIONS
# ──────────────────────────────────────────────────────────────────────

def authenticate_graph() -> Account:
    """
    Authenticate with Microsoft Graph using the Client Credentials flow.
    Returns an authenticated O365 Account object.
    """
    credentials = (CLIENT_ID, CLIENT_SECRET)
    account = Account(
        credentials,
        auth_flow_type="credentials",   # client-credentials grant
        tenant_id=TENANT_ID,
    )
    if account.authenticate():
        log.info("✅ Authenticated with Microsoft Graph (Client Credentials)")
        return account
    else:
        raise RuntimeError(
            "❌ Microsoft Graph authentication failed. "
            "Check CLIENT_ID, CLIENT_SECRET, TENANT_ID and app permissions."
        )


def ensure_collection(client: QdrantClient, collection_name: str) -> None:
    """Create the Qdrant collection if it does not already exist."""
    try:
        client.get_collection(collection_name)
        log.info(f"Collection '{collection_name}' already exists.")
    except Exception:
        log.info(f"Creating Qdrant collection '{collection_name}' "
                 f"(dim={EMBEDDING_DIMENSION}, cosine)...")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=models.Distance.COSINE,
            ),
        )


def download_pdfs_from_folder(account: Account, folder_name: str,
                              local_dir: Path) -> list[Path]:
    """
    List all PDF files in the specified OneDrive *root-level* folder
    and download them to ``local_dir``.

    Returns a list of local file paths that were downloaded.
    """
    target_user_email = os.getenv("OD_TARGET_USER_EMAIL")

    if target_user_email:
        # Client Credentials flow targeting a SPECIFIC USER's drive
        # Requires 'Files.Read.All' (Application) permission.
        log.info(f"Targeting OneDrive of user: {target_user_email}")
        # The resource string 'users/{email}' tells MS Graph which user to act on
        storage = account.storage(resource=f"users/{target_user_email}")
    else:
        # Fallback to "me" (context user) or root if no resource specified
        # This usually fails for App-only auth unless targeting a site/drive directly
        storage = account.storage()
    
    try:
        drive = storage.get_default_drive()
        root = drive.get_root_folder()
    except Exception as e:
        log.error(f"❌ Failed to access drive. If using App Auth, ensure 'OD_TARGET_USER_EMAIL' is set. Error: {e}")
        return []

    # Navigate to the target folder
    target_folder = None
    try:
        # Try to find the folder in the root
        for item in root.get_child_folders():
            if item.name == folder_name:
                target_folder = item
                break
    except Exception as e:
         log.error(f"❌ Error accessing root folder: {e}")
         return []

    if target_folder is None:
        log.warning(f"⚠️  Folder '{folder_name}' not found in OneDrive root. "
                    f"Available folders: {[f.name for f in root.get_child_folders()]}")
        return []

    # List PDF items inside the folder
    pdf_items = [
        item for item in target_folder.get_items()
        if item.is_file and item.name.lower().endswith(".pdf")
    ]

    if not pdf_items:
        log.info(f"No PDF files found in '{folder_name}'.")
        return []

    log.info(f"Found {len(pdf_items)} PDF(s) in '{folder_name}'.")

    # Download each file
    downloaded: list[Path] = []
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in pdf_items:
        dest = local_dir / item.name
        item.download(to_path=local_dir, name=item.name)
        log.info(f"   ↓ Downloaded: {item.name}")
        downloaded.append(dest)

    return downloaded


def semantic_chunk_pdf(pdf_path: Path) -> list[Document]:
    """
    Convert a PDF to Markdown (via pymupdf4llm), then split
    semantically by headers before falling back to recursive splitting.

    Mirrors the chunking logic in ``ingestion.py``.
    """
    # ── Step 1: Extract Markdown from PDF ──
    md_text = pymupdf4llm.to_markdown(str(pdf_path))

    log.info(f"   Extracted {len(md_text)} chars of Markdown from {pdf_path.name}")

    # ── Step 2: Split by Markdown headers (preserves section boundaries) ──
    md_headers_to_split = [
        ("#", "H1"),
        ("##", "H2"),
        ("###", "H3"),
    ]
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=md_headers_to_split,
        strip_headers=False,
    )
    header_chunks = header_splitter.split_text(md_text)

    # ── Step 3: Secondary split for chunks still over the size limit ──
    recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=200,
    )
    final_docs = recursive_splitter.split_documents(header_chunks)

    log.info(f"   📄 {pdf_path.name}: {len(final_docs)} semantic chunks "
             f"(from {len(header_chunks)} header-based sections)")

    return final_docs


def ingest_pdf_to_qdrant(
    pdf_path: Path,
    collection_name: str,
    qdrant_client: QdrantClient,
    embeddings: GoogleGenerativeAIEmbeddings,
) -> int:
    """
    Load a single PDF, split it semantically, and upsert vectors into the
    specified Qdrant collection.

    Returns the number of chunks upserted.
    """
    # Semantic chunking pipeline (same as ingestion.py)
    chunks = semantic_chunk_pdf(pdf_path)
    if not chunks:
        log.warning(f"   ⚠️  No text extracted from {pdf_path.name}")
        return 0

    # Upsert into Qdrant using LangChain's Qdrant wrapper
    vector_store = Qdrant(
        client=qdrant_client,
        collection_name=collection_name,
        embeddings=embeddings,
    )
    vector_store.add_documents(chunks)

    return len(chunks)


# ──────────────────────────────────────────────────────────────────────
# 5.  MAIN ETL PIPELINE
# ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("OneDrive → Qdrant   ETL Pipeline  (START)")
    log.info("=" * 60)

    # --- Authenticate with Microsoft Graph ---
    account = authenticate_graph()

    # --- Initialise shared objects (same as ingestion.py) ---
    qdrant_client = QdrantClient(url=QDRANT_URL)
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GOOGLE_API_KEY,
    )

    base_temp = Path("./temp")
    total_chunks = 0

    # --- Iterate through each folder → collection pair ---
    for folder_name, collection_name in FOLDER_MAP.items():
        log.info("-" * 50)
        log.info(f"📁 Processing folder: '{folder_name}' → collection: '{collection_name}'")

        # Ensure the Qdrant collection exists
        ensure_collection(qdrant_client, collection_name)

        # Download PDFs from OneDrive
        local_dir = base_temp / folder_name
        downloaded_files = download_pdfs_from_folder(account, folder_name, local_dir)

        if not downloaded_files:
            continue

        # Ingest each PDF
        for pdf_path in downloaded_files:
            try:
                n = ingest_pdf_to_qdrant(
                    pdf_path=pdf_path,
                    collection_name=collection_name,
                    qdrant_client=qdrant_client,
                    embeddings=embeddings,
                )
                total_chunks += n
            except Exception as exc:
                log.error(f"   ❌ Failed to ingest {pdf_path.name}: {exc}")
            finally:
                # Clean up the temporary file regardless of success/failure
                if pdf_path.exists():
                    pdf_path.unlink()
                    log.info(f"   🗑️  Cleaned up: {pdf_path.name}")

        # Remove the now-empty temp sub-directory
        if local_dir.exists():
            shutil.rmtree(local_dir, ignore_errors=True)

    # Remove the base temp directory if empty
    if base_temp.exists():
        shutil.rmtree(base_temp, ignore_errors=True)

    log.info("=" * 60)
    log.info(f"✅ ETL complete — {total_chunks} total chunks upserted.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
