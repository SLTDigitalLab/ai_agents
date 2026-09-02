import os
import shutil
import tempfile
import logging
import asyncio
import hashlib
import re
import sys
import fitz
from pathlib import Path

# Support direct CLI execution: `python services/ingestion.py <path>`.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import APIRouter, UploadFile, BackgroundTasks
from pydantic import BaseModel
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from core.llm import get_embedding_model
from langchain_text_splitters import HTMLHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_unstructured import UnstructuredLoader
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient, models
import pytesseract

from services.visual_extractor import process_pdf_visuals, process_document_visuals

# Explicitly set Tesseract path for Windows environments
if sys.platform == "win32":
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'



from core.config import evidence_storage_dir, settings, agent_collection_name

log = logging.getLogger(__name__)

router = APIRouter()

class IngestionService:
    def __init__(self):
        # 1. Initialize the Embedding Model from Factory
        self.embeddings = get_embedding_model()

        # 2. Sparse embedding model (BM25) for hybrid search.
        # Combines lexical matching with dense semantic search - essential
        # for catching exact product codes, SKUs, IDs, and proper nouns
        # that dense embeddings often mis-rank.
        self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

        # 3. Initialize Qdrant Client
        self.client = QdrantClient(url=settings.QDRANT_URL, check_compatibility=False, timeout=60.0)

        # 4. Secondary splitter for chunks that are still too large.
        # chunk_size is aligned with Unstructured's max_characters (1800) so
        # the two splitters target the same size instead of the recursive
        # splitter re-cutting Unstructured's semantic chunks by raw character
        # count. It now only fires for xlsx sheets and web pages, which do not
        # go through Unstructured's by_title chunking.
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1800,
            chunk_overlap=300,
        )

        # Tables are kept whole up to this size so the header row is never
        # orphaned from its data rows. Larger tables are split with the header
        # repeated in each part. The cap keeps a single table within the
        # embedding model's input limit.
        self.TABLE_KEEP_WHOLE_MAX = 6000

    def _ensure_collection_exists(self, collection_name: str):
        """Manually checks if a collection exists. If not, creates it safely.

        Collections are created with NAMED dense + sparse vector configs to
        support hybrid retrieval (dense semantic + BM25 lexical).
        """
        try:
            self.client.get_collection(collection_name)
        except Exception:
            print(f"Collection '{collection_name}' not found. Creating it manually...")
            # Use the dimension size from settings (default 3072 for Gemini)
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=settings.EMBEDDING_DIMENSIONS,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False),
                    ),
                },
            )

    async def ingest_website(self, url: str, agent_name: str):
        """Async wrapper — offloads the blocking work to a worker thread.

        The actual loading, splitting and Qdrant upsert are synchronous and
        CPU/IO-bound. Running them directly on the event loop would freeze
        every chat stream until ingestion finished, so we push the work to a
        thread and keep the loop free to serve agents.
        """
        return await asyncio.to_thread(self._ingest_website_sync, url, agent_name)

    def _ingest_website_sync(self, url: str, agent_name: str):
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
                # Surface the section breadcrumb as the chunk title so
                # citations name the section the answer came from.
                doc.metadata["title"] = breadcrumb
                if not doc.page_content.startswith(breadcrumb):
                    doc.page_content = f"[Section: {breadcrumb}]\n{doc.page_content}"
            doc.metadata["link"] = url

        # Define Collection Name (namespaced by embedding provider)
        collection_name = agent_collection_name(agent_name)

        # Create collection manually first
        self._ensure_collection_exists(collection_name)

        # Upsert to Qdrant (hybrid: dense + sparse)
        vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
            sparse_embedding=self.sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )
        vector_store.add_documents(docs)

        return {
            "status": "success",
            "message": f"Ingested {len(docs)} chunks from {url} for agent {agent_name}",
        }

        # Cleaned up _authenticate_graph method

    def _load_xlsx(self, file_path: Path) -> list[Document]:
        """Read an .xlsx file sheet-by-sheet with pandas.

        Each sheet becomes one Document whose text is a markdown table.
        The recursive splitter downstream breaks oversized sheets into
        chunks, preserving the sheet-name metadata.
        """
        import pandas as pd

        sheets = pd.read_excel(file_path, sheet_name=None, dtype=str, engine="openpyxl")
        docs: list[Document] = []
        for sheet_name, df in sheets.items():
            df = df.fillna("")
            if df.empty:
                continue
            text = f"[Sheet: {sheet_name}]\n{df.to_csv(index=False)}"
            docs.append(
                Document(
                    page_content=text,
                    metadata={"source": str(file_path), "sheet": sheet_name},
                )
            )
        return docs

    def _extract_visual_documents(self, file_path: Path, doc_id: str) -> list[Document]:
        """Extract visual elements from multiple formats (PDF, DOCX, PPTX, XLSX)."""
        visual_docs = []
        file_ext = file_path.suffix.lower()
        
        # Process visual content for all supported formats
        if file_ext in (".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"):
            try:
                from services.visual_extractor import process_document_visuals
                
                records, doc_chunks = process_document_visuals(file_path=str(file_path), doc_id=doc_id)
                
                # Add extracted visual documents to the list
                visual_docs.extend(doc_chunks)
                
                log.info(f"✅ Extracted {len(doc_chunks)} visual elements from {file_path.name}")
            except Exception as e:
                log.warning(f"Visual extraction skipped/failed for {file_path.name}: {e}")

            # For PDFs, also generate evidence crops for display
            if file_ext == ".pdf":
                try:
                    doc = fitz.open(str(file_path))
                    total_pages = len(doc)
                    doc.close()

                    for page_num in range(1, total_pages + 1):
                        # This explicitly triggers saving crop PNGs into storage/evidence/
                        previews = self._render_pdf_evidence_previews(
                            file_path=file_path,
                            page_number=page_num,
                            max_crops=6
                        )
                        
                        # Attach preview URL metadata to extracted documents
                        for prev in previews:
                            visual_docs.append(
                                Document(
                                    page_content=f"[Visual Evidence Crop from Page {page_num}]",
                                    metadata={
                                        "doc_id": doc_id,
                                        "source": file_path.name,
                                        "page_number": page_num,
                                        "evidence_url": prev["url"],
                                        "type": "visual_crop",
                                    }
                                )
                            )
                except Exception as e:
                    log.error(f"Failed to render local evidence crops for {file_path}: {e}")

        return visual_docs
    

    def _load_with_strategy(self, file_path: Path, strategy: str) -> list[Document]:
        """Run UnstructuredLoader with a specific strategy."""
        loader = UnstructuredLoader(
            file_path=str(file_path),
            chunking_strategy="by_title",
            max_characters=1800,
            combine_text_under_n_chars=500,
            strategy=strategy,
            languages=["eng", "sin"],
        )
        return loader.load()

    def _evidence_storage_dir(self) -> Path:
        """Return the local directory used to store generated evidence previews."""
        evidence_dir = evidence_storage_dir()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        return evidence_dir

    def _safe_slug(self, value: str, max_len: int = 80) -> str:
        """Create a filesystem-safe slug from a filename/title."""
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
        return slug[:max_len] or "evidence"

    def _metadata_page_number(self, metadata: dict) -> int | None:
        """Extract page number from Unstructured metadata if available."""
        for key in ("page_number", "page"):
            value = metadata.get(key)
            if value is None:
                continue
            try:
                page = int(value)
                return page if page > 0 else None
            except Exception:
                continue
        return None
    
    def _expand_pdf_rect(self, rect, page_rect, margin: float = 8):
        """Expand a PyMuPDF rectangle safely inside page bounds."""
        import fitz

        return fitz.Rect(
            max(page_rect.x0, rect.x0 - margin),
            max(page_rect.y0, rect.y0 - margin),
            min(page_rect.x1, rect.x1 + margin),
            min(page_rect.y1, rect.y1 + margin),
        )

    
    def _rect_overlap_ratio(self, a, b) -> float:
        """Return overlap ratio against the smaller rectangle area."""
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)

        if x1 <= x0 or y1 <= y0:
            return 0.0

        intersection_area = (x1 - x0) * (y1 - y0)
        smaller_area = min(a.width * a.height, b.width * b.height)

        if smaller_area <= 0:
            return 0.0

        return intersection_area / smaller_area

    def _is_reasonable_visual_rect(self, rect, page_rect) -> bool:
        """Check whether a rectangle is likely to be a real visual/table area."""
        page_area = page_rect.width * page_rect.height
        rect_area = rect.width * rect.height

        if rect_area <= 0:
            return False

        # Avoid tiny icons/lines.
        if rect.width < page_rect.width * 0.12:
            return False

        if rect.height < page_rect.height * 0.045:
            return False

        # Avoid full-page crops.
        if rect_area > page_area * 0.75:
            return False

        # Avoid document header/footer zones.
        if rect.y0 < page_rect.height * 0.09:
            return False

        if rect.y1 > page_rect.height * 0.95:
            return False

        return True

    def _cluster_pdf_rects(self, rects, page_rect, gap: float = 12):
        """Cluster nearby drawing rectangles into larger visual/table regions."""
        import fitz

        if not rects:
            return []

        clusters = [fitz.Rect(rect) for rect in rects]
        changed = True

        while changed:
            changed = False
            merged_clusters = []
            used = [False] * len(clusters)

            for i, base in enumerate(clusters):
                if used[i]:
                    continue

                current = fitz.Rect(base)
                used[i] = True

                for j in range(i + 1, len(clusters)):
                    if used[j]:
                        continue

                    expanded_current = self._expand_pdf_rect(current, page_rect, margin=gap)
                    expanded_other = self._expand_pdf_rect(clusters[j], page_rect, margin=gap)

                    if expanded_current.intersects(expanded_other):
                        current |= clusters[j]
                        used[j] = True
                        changed = True

                merged_clusters.append(current)

            clusters = merged_clusters

        return clusters

    def _dedupe_pdf_rects(self, rects, page_rect):
        """Remove duplicate/overlapping crop candidates."""
        cleaned = []

        for rect in sorted(rects, key=lambda r: (r.y0, r.x0, -(r.width * r.height))):
            if not self._is_reasonable_visual_rect(rect, page_rect):
                continue

            duplicate = False

            for existing in cleaned:
                if self._rect_overlap_ratio(rect, existing) >= 0.75:
                    duplicate = True
                    break

            if not duplicate:
                cleaned.append(rect)

        return cleaned

    def _caption_search_terms(self, source_doc: Document | None) -> list[str]:
        """Build likely caption/search terms from the chunk text."""
        text = source_doc.page_content if source_doc else ""
        terms: list[str] = []

        patterns = [
            r"\bIllustration\s+\d+",
            r"\bFigure\s+\d+",
            r"\bTable\s+\d+(?:\.\d+)?",
            r"\bTable\s+[IVXLCDM]+",
            r"\bAnnexure\s+\d+",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = match.group(0).strip()
                if value and value not in terms:
                    terms.append(value)

        for term in ["Illustration", "Figure", "Table", "Flowchart", "Diagram"]:
            if term.lower() in text.lower() and term not in terms:
                terms.append(term)

        return terms

    def _find_caption_rects(self, page, source_doc: Document | None):
        """Find caption/title locations on the PDF page."""
        matches = []

        for term in self._caption_search_terms(source_doc):
            try:
                found = page.search_for(term)
            except Exception:
                found = []

            matches.extend(found)

        return sorted(matches, key=lambda r: (r.y0, r.x0))
    
    def _find_caption_based_table_rects(self, page, source_doc: Document | None, max_crops: int = 6):
        """Find table crops using table captions and nearby text blocks.

        This helps with borderless/text-style tables where PyMuPDF cannot
        detect table lines or drawing rectangles.
        """
        import fitz

        page_rect = page.rect
        table_terms = [
            term for term in self._caption_search_terms(source_doc)
            if term.lower().startswith("table")
        ]

        if not table_terms:
            return []

        caption_rects = []
        for term in table_terms:
            try:
                found = page.search_for(term)
            except Exception:
                found = []
            caption_rects.extend(found)

        caption_rects = sorted(caption_rects, key=lambda r: (r.y0, r.x0))
        if not caption_rects:
            return []

        try:
            raw_blocks = page.get_text("blocks")
        except Exception:
            raw_blocks = []

        text_blocks = []
        for block in raw_blocks:
            if len(block) < 5:
                continue

            x0, y0, x1, y1, block_text = block[:5]
            block_text = str(block_text or "").strip()
            if not block_text:
                continue

            rect = fitz.Rect(x0, y0, x1, y1)

            if rect.y0 < page_rect.height * 0.09:
                continue
            if rect.y1 > page_rect.height * 0.95:
                continue

            text_blocks.append((rect, block_text))

        crops = []

        for caption_rect in caption_rects:
            related_rects = [caption_rect]
            max_bottom = min(page_rect.y1 * 0.93, caption_rect.y1 + 190)

            for block_rect, block_text in text_blocks:
                if block_rect.y0 < caption_rect.y0 - 4:
                    continue
                if block_rect.y0 > max_bottom:
                    continue

                if (
                    block_rect.y0 > caption_rect.y1 + 8
                    and re.match(
                        r"^\s*table\s+(\d+(\.\d+)?|[ivxlcdm]+)",
                        block_text.lower(),
                        flags=re.IGNORECASE,
                    )
                ):
                    break

                related_rects.append(block_rect)

            crop_rect = fitz.Rect(related_rects[0])
            for rect in related_rects[1:]:
                crop_rect |= rect

            crop_rect = self._expand_pdf_rect(crop_rect, page_rect, margin=10)

            crop_area = crop_rect.width * crop_rect.height
            page_area = page_rect.width * page_rect.height

            if crop_area <= 0:
                continue
            if crop_rect.width < page_rect.width * 0.25:
                continue
            if crop_rect.height < page_rect.height * 0.035:
                continue
            if crop_area > page_area * 0.70:
                continue

            crops.append(crop_rect)

        return self._dedupe_pdf_rects(crops, page_rect)[:max_crops]
    
    def _pdf_rect_text(self, page, rect) -> str:
        """Extract text inside a PDF crop rectangle."""
        try:
            text = page.get_text("text", clip=rect) or ""
        except Exception:
            return ""

        return text.strip()

    def _is_useful_evidence_crop(self, page, rect, source_doc: Document | None = None) -> bool:
        """Reject low-value crops such as text paragraphs, TOC/revision tables, and empty control tables."""
        raw_text = self._pdf_rect_text(page, rect)
        text = re.sub(r"\s+", " ", raw_text).strip().lower()

        # If it has no text, it may still be a useful chart/image.
        if not text:
            return True

        low_value_phrases = (
            "table of content",
            "table of contents",
            "reference table of changes",
            "changes made in the document",
            "old version of the document",
            "date changes made to document",
            "paragraph no. where changes made",
            "detailed description of the changes made",
            "document preparation",
            "controlled circulation",
            "issue no",
            "revision no",
            "date of issue",
            "date of revision",
            "internal use only",
            "unauthorized reproduction",
            "page intentionally left blank",
        )

        if any(phrase in text for phrase in low_value_phrases):
            return False

        words = re.findall(r"[a-zA-Z]{2,}", text)
        word_count = len(words)

        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        long_lines = sum(1 for line in lines if len(line) > 90)

        has_table_caption = bool(
            re.search(r"\btable\s+(\d+(\.\d+)?|[ivxlcdm]+)\b", text, flags=re.IGNORECASE)
        )
        has_visual_keyword = any(
            word in text
            for word in (
                "illustration",
                "figure",
                "diagram",
                "chart",
                "graph",
                "flowchart",
                "process flow",
            )
        )

        # Reject paragraph-heavy crops. Real table/chart crops should be compact.
        if word_count > 180:
            return False

        if word_count > 130 and long_lines >= 3 and not has_visual_keyword:
            return False

        # Reject normal policy section paragraphs like 3.8.4 / 3.8.5 etc.
        section_heading_hits = len(
            re.findall(r"\b\d+(?:\.\d+){1,}\s+[A-Za-z]", raw_text)
        )
        if section_heading_hits >= 2 and word_count > 80 and not has_table_caption and not has_visual_keyword:
            return False

        return True

    def _find_visual_crop_rects(self, page, source_doc: Document | None, max_crops: int = 6):
        """Find multiple likely image/chart/table crop regions on the page."""
        import fitz

        page_rect = page.rect
        candidates = []

        # 1. Embedded image blocks.
        try:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") == 1:  # image block
                    rect = fitz.Rect(block.get("bbox"))
                    if self._is_reasonable_visual_rect(rect, page_rect):
                        candidates.append(rect)
        except Exception as e:
            log.debug(f"Image block detection failed: {e}")

                # 2. Native table detection.
        # This catches many real PDF tables more tightly than drawing clustering.
        try:
            table_finder = page.find_tables()
            tables = getattr(table_finder, "tables", table_finder)

            for table in tables:
                bbox = getattr(table, "bbox", None)
                if not bbox:
                    continue

                rect = fitz.Rect(bbox)

                if self._is_reasonable_visual_rect(rect, page_rect):
                    candidates.append(rect)

        except Exception as e:
            log.debug(f"Native table detection failed: {e}")

        # 2b. Vector drawings/flowchart shapes.
        drawing_rects = []
        try:
            for drawing in page.get_drawings():
                raw_rect = drawing.get("rect")
                if not raw_rect:
                    continue

                rect = fitz.Rect(raw_rect)

                # Exclude header/footer decoration lines.
                if rect.y0 < page_rect.height * 0.09:
                    continue
                if rect.y1 > page_rect.height * 0.95:
                    continue

                # Ignore extremely tiny drawing fragments.
                if rect.width < 2 and rect.height < 2:
                    continue

                drawing_rects.append(rect)
        except Exception as e:
            log.debug(f"Drawing detection failed: {e}")

        if drawing_rects:
            clusters = self._cluster_pdf_rects(drawing_rects, page_rect, gap=14)
            for rect in clusters:
                if self._is_reasonable_visual_rect(rect, page_rect):
                    candidates.append(rect)

        # 2c. Form XObjects — catches some charts/diagrams embedded as vector groups.
        try:
            doc_ref = page.parent

            for xobj in page.get_xobjects():
                xref = xobj[0] if len(xobj) > 0 else None
                bbox = xobj[3] if len(xobj) > 3 else None

                if not xref or not bbox:
                    continue

                subtype = doc_ref.xref_get_key(xref, "Subtype")[1]

                if subtype == "/Form":
                    rect = fitz.Rect(bbox)

                    if self._is_reasonable_visual_rect(rect, page_rect):
                        candidates.append(rect)

        except Exception as e:
            log.debug(f"Form XObject detection failed: {e}")

        # 3. Caption-based table crops for borderless/text-style tables.
        caption_table_rects = self._find_caption_based_table_rects(
            page=page,
            source_doc=source_doc,
            max_crops=max_crops,
        )
        candidates.extend(caption_table_rects)

        candidates = self._dedupe_pdf_rects(candidates, page_rect)

        if not candidates:
            return []

        caption_rects = self._find_caption_rects(page, source_doc)

        # Prefer crops near captions first, but still keep other valid crops.
        caption_ranked = []
        remaining = list(candidates)

        for caption_rect in caption_rects:
            near = [
                rect for rect in remaining
                if rect.y0 >= caption_rect.y0 - 12
            ]

            if not near:
                continue

            best = sorted(
                near,
                key=lambda r: (
                    abs(r.y0 - caption_rect.y1),
                    -(r.width * r.height),
                ),
            )[0]

            caption_ranked.append(best)
            remaining.remove(best)

        # Then add remaining crops in reading order.
        ordered = caption_ranked + sorted(remaining, key=lambda r: (r.y0, r.x0))

        # Expand safely.
        expanded = [
            self._expand_pdf_rect(rect, page_rect, margin=10)
            for rect in ordered
        ]

        # Final quality filter: remove text-heavy / low-value crops.
        useful = [
            rect for rect in expanded
            if self._is_useful_evidence_crop(page, rect, source_doc)
        ]

        return useful[:max_crops]

    def _render_pdf_evidence_previews(
        self,
        file_path: Path,
        page_number: int,
        source_doc: Document | None = None,
        max_crops: int = 6,
    ) -> list[dict]:
        """Render cropped PDF image/table evidence previews.

        If multiple images/tables exist on one page, this creates multiple
        cropped PNGs. If no valid crop is detected, it returns no visual
        evidence instead of saving a full PDF page.
        """
        if page_number <= 0:
            return []

        try:
            import fitz  # PyMuPDF

            evidence_dir = self._evidence_storage_dir()
            file_hash = hashlib.sha1(file_path.name.encode("utf-8")).hexdigest()[:10]
            base_name = self._safe_slug(file_path.stem)

            rendered_items: list[dict] = []

            doc = fitz.open(str(file_path))
            try:
                page_index = page_number - 1
                if page_index < 0 or page_index >= len(doc):
                    return []

                page = doc.load_page(page_index)
                crop_rects = self._find_visual_crop_rects(
                    page=page,
                    source_doc=source_doc,
                    max_crops=max_crops,
                )

                # Do not save full PDF pages as evidence.
                # If no valid image/table/chart crop is detected, skip visual evidence.
                if not crop_rects:
                    return []

                zoom = max(2.0, float(settings.EVIDENCE_RENDER_ZOOM))
                matrix = fitz.Matrix(zoom, zoom)

                for idx, crop_rect in enumerate(crop_rects, start=1):
                    pix = page.get_pixmap(clip=crop_rect, dpi=150)
                    filename = f"{base_name}_p{page_number}_crop{idx}_{file_hash}.png"
                    output_path = evidence_dir / filename

                    pix.save(str(output_path))

                    # Store relative URL path for API/frontend rendering
                    relative_url = f"{settings.EVIDENCE_URL_PREFIX}/{filename}"

                    rendered_items.append({
                        "crop_index": idx,
                        "path": str(output_path),
                        "url": relative_url
                    })

                return rendered_items

            finally:
                doc.close()


        except Exception as e:
            log.error(f"Error rendering PDF evidence previews for {file_path}: {e}")
            return []

    def _doc_has_visual_or_image_signal(self, doc: Document) -> bool:
        """Best-effort check whether a chunk is related to an image/chart/diagram."""
        metadata = doc.metadata or {}
        category = str(metadata.get("category", "")).lower()
        text = (doc.page_content or "").lower()

        visual_categories = {
            "image",
            "figure",
            "figurecaption",
            "caption",
        }

        visual_words = (
            "figure",
            "illustration",
            "diagram",
            "chart",
            "graph",
            "image",
            "flowchart",
            "process flow",
        )

        return (
            category in visual_categories
            or any(word in text for word in visual_words)
        )

    def _doc_has_table_signal(self, doc: Document) -> bool:
        """Best-effort check whether a chunk is a table or table-like content."""
        metadata = doc.metadata or {}
        category = str(metadata.get("category", "")).lower()
        text = doc.page_content or ""
        text_lower = text.lower()

        low_value_table_phrases = (
            "reference table of changes",
            "changes made in the document",
            "old version of the document",
            "date changes made to document",
            "paragraph no. where changes made",
            "detailed description of the changes made",
            "table of content",
            "table of contents",
        )

        if any(phrase in text_lower for phrase in low_value_table_phrases):
            return False

        if category in ("table", "tablechunk"):
            return True

        # Official table captions: Table 01, Table 3.1, Table IV, etc.
        if re.search(r"\btable\s+(\d+(\.\d+)?|[ivxlcdm]+)\b", text_lower):
            return True

        table_keywords = (
            "criteria",
            "minimum service period",
            "duration",
            "weightage",
            "reference rating",
            "achievement",
            "eligibility",
            "max. amount",
            "benefit",
            "percentage allocated",
            "performance rating",
            "staff level",
            "target component",
        )

        keyword_hits = sum(1 for word in table_keywords if word in text_lower)
        if keyword_hits >= 2:
            return True

        # Simple table-like signal: multiple rows with separators/tabs/spaced columns
        lines = [line for line in text.splitlines() if line.strip()]
        separator_rows = sum(
            1 for line in lines
            if "\t" in line or "|" in line or len(re.split(r"\s{2,}", line.strip())) >= 3
        )

        return len(lines) >= 3 and separator_rows >= 2

    def _build_table_evidence(self, doc: Document, file_name: str, link: str) -> dict | None:
        """Create table evidence metadata for a table-like chunk."""
        if not self._doc_has_table_signal(doc):
            return None

        page_number = self._metadata_page_number(doc.metadata or {})

        return {
            "type": "table",
            "title": f"Table from {file_name}",
            "source": file_name,
            "link": link,
            "page": page_number,
            "content": (doc.page_content or "").strip()[:5000],
        }

    def _build_image_evidence(
        self,
        doc: Document,
        file_path: Path,
        file_name: str,
        link: str,
        render_cache: dict[tuple[str, int], list[dict]] | None = None,
    ) -> list[dict] | None:
        """Create cropped image/table evidence metadata for visual PDF content."""
        if file_path.suffix.lower() != ".pdf":
            return None

        has_visual_signal = self._doc_has_visual_or_image_signal(doc)
        has_table_signal = self._doc_has_table_signal(doc)

        if not has_visual_signal and not has_table_signal:
            return None

        page_number = self._metadata_page_number(doc.metadata or {})
        if not page_number:
            return None

        cache_key = (str(file_path), page_number)

        if render_cache is not None and cache_key in render_cache:
            rendered_items = render_cache[cache_key]
        else:
            rendered_items = self._render_pdf_evidence_previews(
                file_path=file_path,
                page_number=page_number,
                source_doc=doc,
                max_crops=6,
            )

            if render_cache is not None:
                render_cache[cache_key] = rendered_items

        if not rendered_items:
            return None

        evidence_kind = "table" if has_table_signal and not has_visual_signal else "visual"
        title_prefix = "Table reference" if evidence_kind == "table" else "Visual reference"

        evidence_items: list[dict] = []

        for rendered in rendered_items:
            crop_index = rendered.get("crop_index")

            title = f"{title_prefix} from {file_name}"
            if len(rendered_items) > 1:
                title = f"{title_prefix} {crop_index} from {file_name}"

            evidence_items.append({
                "type": "image",
                "title": title,
                "source": file_name,
                "link": link,
                "page": page_number,
                "url": rendered["url"],
                "crop_index": crop_index,
                "evidence_kind": evidence_kind,
            })

        return evidence_items

    def _derive_section_heading(self, text: str) -> str | None:
        """Best-effort section heading for a chunk.

        Unstructured's ``by_title`` chunking starts each composite chunk at a
        section Title element, so the first non-empty line of the chunk is
        usually the heading (e.g. "3.8 Maternity Leave"). We surface that as
        the chunk ``title`` for citations.

        Returns ``None`` when the first line does not look like a heading
        (too long / sentence-like / no letters), so callers can fall back to
        the filename instead of stamping a paragraph as a title.
        """
        if not text:
            return None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Only the first non-empty line is a heading candidate; a long
            # first line is prose, not a title.
            if len(line) > 120:
                return None

            # A trailing period signals a sentence or continuation fragment
            # (a chunk that starts mid-paragraph), not a section heading.
            if line.endswith("."):
                return None

            heading = line.rstrip(" :;-")

            # Require at least one letter so we don't title a chunk "1." or "•".
            if not re.search(r"[A-Za-z]", heading):
                return None

            return heading

        return None

    def _is_table_doc(self, doc: Document) -> bool:
        """Decide whether a chunk should be treated as a structured table.

        Uses only precise signals (Unstructured's element category, captured
        table HTML, or an xlsx sheet) — not the looser keyword heuristic used
        for evidence — so prose is never mistaken for a table here.
        """
        md = doc.metadata or {}
        category = str(md.get("category", "")).lower()
        return (
            category in ("table", "tablechunk")
            or bool(md.get("text_as_html"))
            or bool(md.get("sheet"))
        )

    def _render_html_table(self, html: str) -> str:
        """Render Unstructured's ``text_as_html`` into a pipe-delimited grid.

        One row per line keeps each value bound to its row, so the flattened
        cell stream (which can drop a column value into the wrong cell) is
        replaced with a layout the embedding and the LLM can read row-wise.
        """
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return ""

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return ""

        rows_out: list[str] = []
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            values = [
                re.sub(r"\s+", " ", cell.get_text(" ").strip())
                for cell in cells
            ]
            if any(values):
                rows_out.append(" | ".join(values))

        return "\n".join(rows_out)

    def _apply_table_grid(self, doc: Document) -> None:
        """Replace a table chunk's flattened text with a structured grid.

        Only acts when the loader captured ``text_as_html``; otherwise the
        chunk text is left untouched (e.g. OCR tables whose structure was
        never detected).
        """
        html = (doc.metadata or {}).get("text_as_html")
        if not html:
            return

        grid = self._render_html_table(html)
        if grid:
            doc.page_content = grid

    def _split_table_with_header(self, doc: Document) -> list[Document]:
        """Split an oversized table, repeating its header in every sub-chunk.

        Prevents the header row from surviving only in the first part. For a
        single-line flattened table (no row breaks) there is no header to
        repeat, so it falls back to a plain recursive split.
        """
        lines = doc.page_content.splitlines()

        header_lines: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            header_lines.append(line)
            # A leading "[Sheet: …]"/"[Section: …]" marker is kept together
            # with the real header row that follows it.
            if not line.strip().startswith("["):
                break
            if len(header_lines) >= 2:
                break

        header = "\n".join(header_lines)
        sub_chunks = self.recursive_splitter.split_documents([doc])

        if not header:
            return sub_chunks

        for sub in sub_chunks:
            if not sub.page_content.startswith(header):
                sub.page_content = f"{header}\n{sub.page_content}"

        return sub_chunks

    def _load_and_chunk_file(self, file_path: Path, doc_id: str = None) -> list[Document]:
        """Use unstructured's native semantic chunking by headers and sections.

        Strategy ladder:
          1. "fast"    — pulls embedded text layer. Seconds per PDF,
                         low memory. Works for most digital PDFs and
                         all .docx/.pptx/.xlsx.
          2. "hi_res"  — layout detection + OCR (English + Sinhala).
                         Minutes per PDF, GB of RAM. Used only when
                         "fast" yields no/minimal text (scanned PDFs).
        """
        ext = file_path.suffix.lower()

        # Excel: read with pandas. Unstructured partitions every cell and
        # spams "No features in text" for empty cells, which is both slow
        # and low-signal on tabular invoice sheets.
        if ext == ".xlsx":
            log.info(f"   📊 Excel file detected ({file_path.name}).")
            docs = self._load_xlsx(file_path)
        # Images always need OCR
        elif ext in (".png", ".jpg", ".jpeg"):
            docs = self._load_with_strategy(file_path, "hi_res")
        else:
            # 1. Try fast first
            try:
                docs = self._load_with_strategy(file_path, "fast")
            except Exception as e:
                log.warning(f"fast strategy failed for {file_path.name}: {e}; falling back to hi_res")
                docs = []

            total_chars = sum(len(d.page_content) for d in docs)
            # 2a. Whole document has no usable text layer (fully scanned) —
            # OCR the entire file in one hi_res pass.
            if total_chars < 50:
                log.info(
                    f"{file_path.name}: fast produced {total_chars} chars — "
                    f"falling back to hi_res + OCR."
                )
                try:
                    docs = self._load_with_strategy(file_path, "hi_res")
                except Exception as e:
                    log.error(f"hi_res strategy failed for {file_path.name}: {e}")
                    docs = []
            # 2b. Partially scanned PDF — the fast pass got text from most
            # pages, but some scanned pages produced little/none. OCR only
            # those pages instead of silently dropping them.
            elif ext == ".pdf":
                docs = self._ocr_missing_pdf_pages(file_path, docs)


        #
        for doc in docs:
            doc.metadata["type"] = "text"
            if doc_id:
                doc.metadata["doc_id"] = doc_id        

        # Re-split any chunks that are still too large after semantic
        # chunking.  Oversized chunks dilute embedding precision because
        # the vector becomes an average of multiple concepts.  The
        # recursive splitter preserves metadata and adds overlap so
        # answers that straddle a boundary are not lost.
        final_docs = []
        for doc in docs:
            # Tables get structure-aware handling: render the captured grid and
            # never split by raw character count (which orphans the header row).
            is_table = self._is_table_doc(doc)
            if is_table:
                self._apply_table_grid(doc)

            # Stamp a best-effort section heading as the chunk title BEFORE any
            # re-split, so oversized chunks propagate it to every sub-chunk
            # (split_documents copies parent metadata onto each child).
            if "title" not in doc.metadata:
                heading = doc.metadata.get("sheet") or self._derive_section_heading(
                    doc.page_content
                )
                if heading:
                    doc.metadata["title"] = heading

            heading = doc.metadata.get("title")

            if is_table:
                # Keep small/medium tables whole; split very large ones while
                # repeating the header row so column labels are never lost.
                if len(doc.page_content) <= self.TABLE_KEEP_WHOLE_MAX:
                    final_docs.append(doc)
                else:
                    final_docs.extend(self._split_table_with_header(doc))
                continue

            if len(doc.page_content) > self.recursive_splitter._chunk_size:
                sub_chunks = self.recursive_splitter.split_documents([doc])

                # Re-splitting drops the section heading from every sub-chunk
                # after the first. Prepend it so each sub-chunk stays
                # self-describing for both embedding and the LLM (mirrors the
                # website breadcrumb behaviour and reduces entity confusion
                # across section boundaries).
                if heading:
                    marker = f"[Section: {heading}]"
                    for sub in sub_chunks:
                        if not sub.page_content.startswith(
                            (heading, marker)
                        ):
                            sub.page_content = f"{marker}\n{sub.page_content}"

                final_docs.extend(sub_chunks)
            else:
                final_docs.append(doc)

        if ext == ".pdf":
            try:
                log.info(f"  👁️ Extracting visual evidence from {file_path.name}...")
                visual_docs = self._extract_visual_documents(file_path, doc_id=doc_id or file_path.stem)
                final_docs.extend(visual_docs)
                log.info(f"  ✅ Added {len(visual_docs)} visual evidence chunks.")
            except Exception as e:
                log.error(f"Visual extraction failed for {file_path.name}: {e}")
        

        return final_docs

    def _normalize_chunk_payloads(
        self, docs: list[Document], doc_id: str, file_name: str
    ) -> list[Document]:
        """Ensures all text and visual chunks strictly adhere to the unified dual-payload schema.
        
        Payload Schema:
        - text: Chunk content (either raw text or visual description)
        - type: 'text' | 'visual_description'
        - doc_id: Unique document identifier
        - source: File name
        - image_path: Local path to PNG evidence asset (only for visual_description)
        - page_number: Page number in original document
        """
        normalized_docs = []

        for doc in docs:
            # Preserve existing metadata while enforcing required unified schema fields
            chunk_type = doc.metadata.get("type", "text")

            updated_metadata = {
                **doc.metadata,
                "doc_id": doc.metadata.get("doc_id", doc_id),
                "source": doc.metadata.get("source", file_name),
                "type": chunk_type,
            }

            # Handle Visual Evidence Specific Metadata
            if chunk_type == "visual_description":
                updated_metadata["image_path"] = doc.metadata.get("image_path", "")
                updated_metadata["page_number"] = doc.metadata.get("page_number", 1)
            else:
                # Text chunks leave image_path empty or None
                updated_metadata["image_path"] = None
                updated_metadata["page_number"] = doc.metadata.get("page_number", 1)

            doc.metadata = updated_metadata
            normalized_docs.append(doc)

        return normalized_docs

    def _ocr_missing_pdf_pages(self, file_path: Path, docs: list[Document]) -> list[Document]:
        """OCR only the scanned pages a 'fast' PDF pass missed.

        The fast text-layer extraction silently produces nothing for scanned
        (image-only) pages. On a PDF that is mostly digital but has a few
        scanned pages, the whole-document char gate passes and those pages are
        lost. This sums chars-per-page from the fast output, finds pages below
        the threshold, and OCRs just those pages — re-mapping their page
        numbers back to the original so citations/evidence stay correct.
        """
        import fitz  # PyMuPDF

        MIN_CHARS_PER_PAGE = 50

        chars_by_page: dict[int, int] = {}
        for d in docs:
            page = self._metadata_page_number(d.metadata or {})
            if page:
                chars_by_page[page] = chars_by_page.get(page, 0) + len(d.page_content or "")

        # No page metadata at all → cannot localise sparse pages. The document
        # already produced text, so leave it as-is rather than OCR everything.
        if not chars_by_page:
            return docs

        try:
            src = fitz.open(str(file_path))
        except Exception as e:
            log.debug(f"Per-page OCR skipped, cannot open {file_path.name}: {e}")
            return docs

        try:
            total_pages = len(src)
            missing_pages = [
                page
                for page in range(1, total_pages + 1)
                if chars_by_page.get(page, 0) < MIN_CHARS_PER_PAGE
            ]

            if not missing_pages:
                return docs

            # If most of the document is sparse, a single whole-file hi_res
            # pass is cheaper than many per-page passes.
            if len(missing_pages) > total_pages * 0.6:
                log.info(
                    f"{file_path.name}: {len(missing_pages)}/{total_pages} pages "
                    f"sparse — running hi_res on the whole file."
                )
                try:
                    return self._load_with_strategy(file_path, "hi_res")
                except Exception as e:
                    log.error(f"Whole-file hi_res failed for {file_path.name}: {e}")
                    return docs

            log.info(
                f"{file_path.name}: OCR-ing {len(missing_pages)} scanned page(s) "
                f"{missing_pages} missed by the fast text layer."
            )

            ocr_docs: list[Document] = []

            for page in missing_pages:
                single = fitz.open()
                try:
                    single.insert_pdf(src, from_page=page - 1, to_page=page - 1)
                    fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
                    os.close(fd)
                    tmp_path = Path(tmp_name)
                    single.save(str(tmp_path))
                finally:
                    single.close()

                try:
                    page_docs = self._load_with_strategy(tmp_path, "hi_res")
                except Exception as e:
                    log.error(
                        f"hi_res OCR failed for {file_path.name} page {page}: {e}"
                    )
                    page_docs = []
                finally:
                    tmp_path.unlink(missing_ok=True)

                # The single-page PDF numbers its elements as page 1; re-map
                # them back to the real page in the source document.
                for page_doc in page_docs:
                    page_doc.metadata["page_number"] = page

                ocr_docs.extend(page_docs)

            return docs + ocr_docs
        finally:
            src.close()

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
        """Async wrapper — offloads the blocking OneDrive ingestion to a thread.

        Downloads, parsing/OCR and embedding are all synchronous and can run
        for minutes. Offloading keeps the event loop free so chat for every
        agent stays responsive while documents are ingested.
        """
        return await asyncio.to_thread(
            self._process_onedrive_sync, folder_id, access_token, agent_name, force
        )

    async def ingest_document_async(self, file_path: Path, agent_name: str, doc_id: str):
        """
        Async wrapper entry point for background tasks.
        Offloads CPU/IO-heavy parsing and vector upserting to a worker thread.
        """
        return await asyncio.to_thread(
            self.ingest_document, file_path, agent_name, doc_id
        )

    def ingest_document(self, file_path: Path, agent_name: str, doc_id: str):
        """
        Synchronous worker method executed by FastAPI BackgroundTasks.
        Parses text, runs PyMuPDF/GPT-4o visual extraction, and upserts to Qdrant.
        """
        log.info(f"🚀 Starting background ingestion for {file_path.name} (Doc ID: {doc_id})")

        try:
            # 1. Run text chunking and PyMuPDF + GPT-4o visual extraction
            chunks = self._load_and_chunk_file(file_path=file_path, doc_id=doc_id)

            if not chunks:
                log.warning(f"No chunks extracted from {file_path.name}")
                return {"status": "warning", "message": "No extractable content found."}

            # 2: Normalize metadata into the Unified Dual-Payload Schema
            final_docs = self._normalize_chunk_payloads(
                docs=chunks, doc_id=doc_id, file_name=file_path.name
            )

            # 3. Ensure target Qdrant collection exists
            collection_name = agent_collection_name(agent_name)
            self._ensure_collection_exists(collection_name)

            # 4. Initialize Qdrant Vector Store with Hybrid Search
            vector_store = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
                embedding=self.embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
                vector_name="dense",
                sparse_vector_name="sparse",
            )

            # 5. Upsert combined text + visual chunks to Qdrant in sub-batches
            batch_size = 15
            total_docs = len(final_docs)

            log.info(f"Uploading {total_docs} documents in batches of {batch_size}...")

            for i in range(0, total_docs, batch_size):
                sub_batch = final_docs[i : i + batch_size]
                vector_store.add_documents(sub_batch)
                log.info(f"  -> Upserted batch {i // batch_size + 1}/{(total_docs + batch_size - 1) // batch_size}")

            log.info(
                f"✅ Successfully ingested {total_docs} total chunks "
                f"(text + visual) into collection '{collection_name}'"
            )

            return {
                "status": "success",
                "chunks_ingested": total_docs,
                "collection": collection_name,
            }

        except Exception as e:
            log.exception(f"❌ Ingestion failed for {file_path.name}: {e}")
            raise e

    def _process_onedrive_sync(self, folder_id: str, access_token: str, agent_name: str, force: bool = False):
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

        # 1. List files in the folder (follow pagination until exhausted).
        # NOTE: do NOT use $select here — @microsoft.graph.downloadUrl is an
        # OData instance annotation and gets dropped when $select is set,
        # which causes every file to be silently skipped.
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

        log.info(f"Graph API returned {len(items)} total items for folder {folder_id}")
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

            # Define Collection Name (namespaced by embedding provider)
            collection_name = agent_collection_name(agent_name)
            self._ensure_collection_exists(collection_name)

            # Initialize Vector Store once (hybrid: dense + sparse)
            vector_store = QdrantVectorStore(
                client=self.client,
                collection_name=collection_name,
                embedding=self.embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
                vector_name="dense",
                sparse_vector_name="sparse",
            )

            skipped_files = []
            failed_files = []
            # Cache rendered PDF crops per (file, page) so multiple chunks on
            # the same page reuse the same image instead of re-rendering.
            evidence_cache: dict[tuple[str, int], list[dict]] = {}

            for item in matching_items:
                file_name = item["name"]
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    log.error(f"No download URL for {file_name}; skipping.")
                    failed_files.append({"file": file_name, "reason": "no download URL from Graph"})
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

                dest_path = temp_dir / file_name

                try:
                    # Download FIRST. Do NOT delete old vectors until we're
                    # sure we have replacement chunks ready to upsert —
                
                    log.info(f"Downloading {file_name}...")
                    file_resp = session.get(download_url, timeout=120)
                    if file_resp.status_code != 200:
                        log.error(
                            f"Failed to download {file_name}: HTTP {file_resp.status_code}"
                        )
                        failed_files.append({"file": file_name, "reason": f"download HTTP {file_resp.status_code}"})
                        continue

                    with open(dest_path, "wb") as f:
                        f.write(file_resp.content)

                    # Chunk using semantic logic
                    chunks = self._load_and_chunk_file(dest_path)
                    if not chunks:
                        # Empty output usually means a scanned/image-only PDF
                        # with no text layer. Surface it so admins can re-run
                        # with OCR instead of silently dropping the file.
                        log.error(
                            f"No chunks produced for {file_name} — likely "
                            f"scanned PDF without text layer or unsupported content."
                        )
                        failed_files.append({"file": file_name, "reason": "no extractable text (OCR needed?)"})
                        continue

                    source_link = item.get("webUrl", "#")

                    for doc in chunks:
                        doc.metadata["source"] = file_name
                        doc.metadata["link"] = source_link
                        doc.metadata["onedrive_id"] = onedrive_id
                        doc.metadata["source_folder"] = folder_id
                        doc.metadata["last_modified"] = last_modified

                        # Attach visual/table evidence (cropped PDF previews)
                        # so retrieval can surface diagrams alongside answers.
                        evidence_items = []

                        table_evidence = self._build_table_evidence(
                            doc=doc,
                            file_name=file_name,
                            link=source_link,
                        )
                        if table_evidence:
                            evidence_items.append(table_evidence)

                        image_evidence_items = self._build_image_evidence(
                            doc=doc,
                            file_path=dest_path,
                            file_name=file_name,
                            link=source_link,
                            render_cache=evidence_cache,
                        )
                        if image_evidence_items:
                            evidence_items.extend(image_evidence_items)

                        if evidence_items:
                            doc.metadata["evidence"] = evidence_items

                    # Now that we have valid replacement chunks, remove the
                    # stale vectors and write the new ones.
                    self._delete_file_vectors(collection_name, onedrive_id, file_name)
                    vector_store.add_documents(chunks)
                    total_chunks += len(chunks)
                    processed_files.append(file_name)
                except Exception as e:
                    log.exception(f"Failed to process {file_name}: {e}")
                    failed_files.append({"file": file_name, "reason": str(e)})
                    continue

            return {
                "status": "success",
                "message": (
                    f"Ingested {total_chunks} chunks from {len(processed_files)} files. "
                    f"Skipped {len(skipped_files)} unchanged files. "
                    f"Failed {len(failed_files)} files."
                ),
                "files": processed_files,
                "skipped": skipped_files,
                "failed": failed_files,
            }


ingestion_service = IngestionService()


@router.post("/upload")
async def upload_document(
    agent_name: str,
    background_tasks: BackgroundTasks,
    file: UploadFile,
):
    """
    Endpoint to receive document uploads and queue ingestion 
    (Text + PyMuPDF/GPT-4o Visual Extraction) in the background.
    """
    # 1. Generate unique file storage location
    file_id = hashlib.md5(file.filename.encode()).hexdigest()[:8]
    temp_dir = Path(tempfile.gettempdir()) / "workmate_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    saved_file_path = temp_dir / f"{file_id}_{file.filename}"

    # 2. Save the uploaded file payload to local disk
    with saved_file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 3. Schedule the ingestion process in the background
    
    background_tasks.add_task(
        ingestion_service.ingest_document, 
        file_path=saved_file_path, 
        agent_name=agent_name,
        doc_id=file_id
    )

    return {
        "status": "queued",
        "message": f"File '{file.filename}' uploaded successfully. Ingestion running in background.",
        "doc_id": file_id,
        "agent_name": agent_name
    }

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Raw-document folders predate the UI agent IDs. Keep those folder names
    # working while indexing into the collections the live agents query.
    LEGACY_FOLDER_AGENT_IDS = {
        "askhr": "hr",
        "askit": "it",
    }

    if len(sys.argv) > 1:
        target_path = Path(sys.argv[1]).resolve()
        service = IngestionService()

        def process_file(file_path: Path, inferred_agent: str):
            if file_path.is_file() and not file_path.name.startswith("."):
                doc_id = file_path.stem
                print(f"Ingesting into [{inferred_agent}]: {file_path.name} (Doc ID: {doc_id})")
                service.ingest_document(file_path, inferred_agent, doc_id)

        if target_path.is_dir():
            # Directory name becomes the agent name (e.g., storage/raw_documents/askit -> askit)
            folder_agent_name = target_path.name.lower()
            agent_name = LEGACY_FOLDER_AGENT_IDS.get(folder_agent_name, folder_agent_name)
            print(f"Detected Agent: '{agent_name}' from folder '{target_path.name}'")
            
            for file_path in target_path.glob("*"):
                process_file(file_path, agent_name)

        elif target_path.is_file():
            # Parent folder name becomes the agent name (e.g., storage/raw_documents/askhr/doc.pdf -> askhr)
            folder_agent_name = target_path.parent.name.lower()
            agent_name = LEGACY_FOLDER_AGENT_IDS.get(folder_agent_name, folder_agent_name)
            print(f"Detected Agent: '{agent_name}' from parent folder '{target_path.parent.name}'")
            
            process_file(target_path, agent_name)

        else:
            print(f"Provided path does not exist: {target_path}")
    else:
        print("Usage: python backend/services/ingestion.py <path_to_directory_or_file>")