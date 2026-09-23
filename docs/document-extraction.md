# Document extraction

Ingestion uses local parsing and Tesseract first. OpenAI vision retries difficult
PDF/image pages before the existing chunking, table handling, and embedding steps.
OpenAI is also the default chat and embedding provider; environment settings take
precedence. Extraction is independent of the embedding model.

Configuration (environment variables):

| Variable | Default | Purpose |
| --- | --- | --- |
| `EXTRACTION_OPENAI_ENABLED` | `true` | Enable selective vision extraction |
| `EXTRACTION_OPENAI_MODEL` | `gpt-4.1` | Vision model used for extraction |
| `EXTRACTION_OPENAI_TIMEOUT_SECONDS` | `90` | Timeout per API attempt; one retry |
| `OPENAI_API_KEY` | unset | Required for OpenAI extraction |

The selector checks extracted text for fewer than 50 non-whitespace characters,
replacement/control characters, and unresolved PDF character IDs. It also checks
PDFs for detected tables without HTML structure and large illustrations on pages
with a usable native text layer. Good OCR of a scanned page alone does not trigger
an API request. This is a heuristic: it cannot detect every wrong character,
misordered column, diagram, or transcription error. Clean standalone images are
left with OCR unless text quality or table metadata triggers the fallback.

Selected pages are rendered and sent to OpenAI's Responses API with `store=false`.
This sends page content to OpenAI and incurs usage charges. Each request returns
separate text/table/visual blocks; visual descriptions are explicitly labeled.
Page numbers and extraction provenance are stored in chunk metadata. The loader
disables cross-page section chunks to allow safe replacement of individual pages
and enables table structure inference for high-resolution OCR.

API errors, refusals, incomplete responses, and empty output retain local text.
Failed PDF retries mark existing page chunks `extraction_needs_review=true` and
log a warning. A model can also set this flag for illegible content. This metadata
is not yet exposed as a dedicated admin review queue. A page without local text
whose API extraction also fails remains unextracted; check ingestion logs. PDFs
without page metadata are preserved and skipped rather than risking replacement
of the wrong content. Missing credentials leave local extraction in place.

Existing indexed files are unchanged until force re-ingestion. After restarting
the backend, try a small representative English/Sinhala sample, inspect the stored
chunks and page citations, and compare tables, dates, amounts, and retrieval
answers before re-ingesting the full collection.

Local tests (mocked API; no charges):

```powershell
venv/Scripts/python.exe -m unittest discover -s backend/tests -p test_document_extraction.py -v
```
