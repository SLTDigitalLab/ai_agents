import os
import shutil
import tempfile
import logging
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from core.llm import get_embedding_model
from langchain_text_splitters import HTMLHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_unstructured import UnstructuredLoader
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
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

        # 2. Sparse embedding model (BM25) for hybrid search.
        # Combines lexical matching with dense semantic search - essential
        # for catching exact product codes, SKUs, IDs, and proper nouns
        # that dense embeddings often mis-rank.
        self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

        # 3. Initialize Qdrant Client
        self.client = QdrantClient(url=settings.QDRANT_URL)

        # 4. Secondary splitter for chunks that are still too large
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1600,
            chunk_overlap=300,
        )

    async def _ensure_collection_exists(self, collection_name: str):
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

    def _load_with_strategy(self, file_path: Path, strategy: str) -> list[Document]:
        """Run UnstructuredLoader with a specific strategy."""
        loader = UnstructuredLoader(
            file_path=str(file_path),
            chunking_strategy="by_title",
            max_characters=2500,
            combine_text_under_n_chars=500,
            strategy=strategy,
            languages=["eng", "sin"],
        )
        return loader.load()
    
    def _evidence_storage_dir(self) -> Path:
        """Return the local directory used to store generated evidence previews."""
        evidence_dir = Path(settings.EVIDENCE_STORAGE_DIR)
        if not evidence_dir.is_absolute():
            evidence_dir = Path(__file__).resolve().parent.parent / evidence_dir

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
                    if not self._is_useful_evidence_crop(page, crop_rect, source_doc):
                        continue

                    output_name = (
                        f"{base_name}-{file_hash}-page-{page_number}-crop-{idx}.png"
                    )
                    output_path = evidence_dir / output_name

                    if not output_path.exists():
                        pix = page.get_pixmap(
                            matrix=matrix,
                            alpha=False,
                            clip=crop_rect,
                        )
                        pix.save(str(output_path))

                    rendered_items.append({
                        "url": f"{settings.EVIDENCE_URL_PREFIX}/{output_name}",
                        "crop_index": idx,
                        "is_crop": True,
                    })

            finally:
                doc.close()

            return rendered_items

        except Exception as e:
            log.warning(
                f"Could not render evidence preview for {file_path.name} page {page_number}: {e}"
            )
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

    def _load_and_chunk_file(self, file_path: Path) -> list[Document]:
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
            # 2. Fall back to hi_res (OCR) when the text layer is missing or nearly empty
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

            # Define Collection Name
            collection_name = f"{agent_name}_docs"
            await self._ensure_collection_exists(collection_name)
            
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
                    # otherwise a download/parse failure wipes existing data.
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


    async def process_sharepoint_ingestion(
        self,
        site_url: str,
        folder_path: str,
        access_token: str,
        agent_name: str,
        force: bool = False,
    ):
        """
        Ingest files from a SharePoint document library folder using Microsoft Graph.

        Simple version:
        - Admin enters SharePoint site URL
        - Admin enters folder path inside the default Documents library
        - Admin enters Graph API token

        Example:
        site_url: https://mysliit.sharepoint.com/sites/WorkmateAITestKB
        folder_path: AI-Ingestion-Test
        """
        import requests
        from urllib.parse import urlparse, quote
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter

        # Setup a resilient session
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        session.mount("http://", HTTPAdapter(max_retries=retries))

        headers = {"Authorization": f"Bearer {access_token}"}

        parsed_site = urlparse(site_url.strip())
        hostname = parsed_site.netloc
        site_path = parsed_site.path.strip("/")

        if not hostname or not site_path:
            return {
                "status": "error",
                "message": "Invalid SharePoint site URL. Example: https://tenant.sharepoint.com/sites/SiteName",
            }

        clean_folder_path = folder_path.strip().strip("/")

        if not clean_folder_path:
            return {
                "status": "error",
                "message": "SharePoint folder path is required. Example: AI-Ingestion-Test",
            }

        encoded_site_path = quote(site_path, safe="/")
        encoded_folder_path = quote(clean_folder_path, safe="/")

        # Step 1: Resolve SharePoint site URL into Graph site ID.
        site_lookup_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{encoded_site_path}"

        try:
            site_resp = session.get(site_lookup_url, headers=headers, timeout=30)

            if site_resp.status_code != 200:
                return {
                    "status": "error",
                    "message": f"SharePoint site lookup failed: {site_resp.status_code} {site_resp.text}",
                }

            site_id = site_resp.json().get("id")

            if not site_id:
                return {
                    "status": "error",
                    "message": "SharePoint site lookup succeeded, but no site ID was returned.",
                }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to resolve SharePoint site URL: {e}",
            }

        # Step 2: Use resolved site ID to list folder children from the default document library.
        url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/drive/root:/{encoded_folder_path}:/children"
            f"?$top=200"
        )

        items = []

        try:
            while url:
                resp = session.get(url, headers=headers, timeout=30)

                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "message": f"SharePoint Graph API Error: {resp.status_code} {resp.text}",
                    }

                payload = resp.json()
                items.extend(payload.get("value", []))
                url = payload.get("@odata.nextLink")

        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to connect to SharePoint Graph API: {e}",
            }

        log.info(
            f"SharePoint Graph API returned {len(items)} total items "
            f"for site_url={site_url}, folder_path={clean_folder_path}"
        )

        ALLOWED_EXTENSIONS = (
            ".pdf",
            ".docx",
            ".pptx",
            ".xlsx",
            ".png",
            ".jpg",
            ".jpeg",
            ".eml",
        )

        matching_items = [
            item for item in items
            if item.get("file") and item.get("name", "").lower().endswith(ALLOWED_EXTENSIONS)
        ]

        if not matching_items:
            return {
                "status": "warning",
                "message": f"No supported files found in SharePoint folder path: {clean_folder_path}",
            }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            total_chunks = 0
            processed_files = []

            collection_name = f"{agent_name}_docs"
            await self._ensure_collection_exists(collection_name)

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
            evidence_cache: dict[tuple[str, int], list[dict]] = {}

            for item in matching_items:
                file_name = item["name"]
                download_url = item.get("@microsoft.graph.downloadUrl")

                if not download_url:
                    log.error(f"No download URL for SharePoint file {file_name}; skipping.")
                    failed_files.append({
                        "file": file_name,
                        "reason": "no download URL from SharePoint Graph",
                    })
                    continue

                sharepoint_item_id = item.get("id", "unknown")
                last_modified = item.get("lastModifiedDateTime", "")

                if not force and self._file_already_ingested(
                    collection_name,
                    sharepoint_item_id,
                    last_modified,
                ):
                    log.info(f"Skipping unchanged SharePoint file: {file_name}")
                    skipped_files.append(file_name)
                    continue

                dest_path = temp_dir / file_name

                try:
                    log.info(f"Downloading SharePoint file {file_name}...")

                    file_resp = session.get(download_url, timeout=120)

                    if file_resp.status_code != 200:
                        log.error(
                            f"Failed to download SharePoint file {file_name}: "
                            f"HTTP {file_resp.status_code}"
                        )
                        failed_files.append({
                            "file": file_name,
                            "reason": f"download HTTP {file_resp.status_code}",
                        })
                        continue

                    with open(dest_path, "wb") as f:
                        f.write(file_resp.content)

                    chunks = self._load_and_chunk_file(dest_path)

                    if not chunks:
                        log.error(
                            f"No chunks produced for SharePoint file {file_name} — "
                            f"likely scanned PDF without text layer or unsupported content."
                        )
                        failed_files.append({
                            "file": file_name,
                            "reason": "no extractable text (OCR needed?)",
                        })
                        continue

                    source_link = item.get("webUrl", "#")

                    for doc in chunks:
                        doc.metadata["source"] = file_name
                        doc.metadata["link"] = source_link

                        # Keep old key so existing skip/delete helpers still work.
                        doc.metadata["onedrive_id"] = sharepoint_item_id

                        # SharePoint metadata
                        doc.metadata["source_type"] = "sharepoint"
                        doc.metadata["sharepoint_site_url"] = site_url
                        doc.metadata["sharepoint_folder_path"] = clean_folder_path
                        doc.metadata["sharepoint_item_id"] = sharepoint_item_id
                        doc.metadata["source_folder"] = clean_folder_path
                        doc.metadata["last_modified"] = last_modified

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

                    self._delete_file_vectors(collection_name, sharepoint_item_id, file_name)

                    vector_store.add_documents(chunks)
                    total_chunks += len(chunks)
                    processed_files.append(file_name)

                except Exception as e:
                    log.exception(f"Failed to process SharePoint file {file_name}: {e}")
                    failed_files.append({"file": file_name, "reason": str(e)})
                    continue

            return {
                "status": "success",
                "message": (
                    f"Ingested {total_chunks} chunks from {len(processed_files)} SharePoint files. "
                    f"Skipped {len(skipped_files)} unchanged files. "
                    f"Failed {len(failed_files)} files."
                ),
                "files": processed_files,
                "skipped": skipped_files,
                "failed": failed_files,
            }
        
ingestion_service = IngestionService()