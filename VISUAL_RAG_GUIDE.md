# Visual RAG Implementation - Complete Guide

## Overview

This document provides a comprehensive guide to the Visual RAG system implementation, including setup, testing, and deployment instructions.

## System Architecture

### Components

1. **Visual Extraction Pipeline** (`backend/services/visual_extractor.py`)
   - Extracts visuals from PDF, DOCX, PPTX, XLSX
   - Uses GPT-4o for visual analysis
   - Stores images and metadata in `./storage/visuals/`

2. **Multi-Format Ingestion** (`backend/services/ingestion.py`)
   - Integrated visual extraction for all document formats
   - Automatic routing to format-specific extractors
   - Metadata preservation in Qdrant

3. **Multimodal KB Agents** (`backend/domain/archetypes/`)
   - `kb_agent.py` - Main KB agent with multimodal support
   - `kb_form_agent.py` - Product/service agent with forms
   - `kb_api_agent.py` - HR agent with API tools
   - Each includes a `multimodal_check` node for GPT-4o integration

4. **Evidence Streaming** (`backend/routers/chat.py`)
   - Thread-local evidence caching
   - SSE JSON serialization for frontend
   - Image path extraction and URL mapping

5. **RAG Retrieval** (`backend/domain/tools/rag_tools.py`)
   - Hybrid search (dense + BM25)
   - Visual evidence collection
   - Image path extraction and conversion

## Pre-Testing Checklist

✅ **Code Changes Completed:**
- [ ] All three KB agents (kb_agent.py, kb_form_agent.py, kb_api_agent.py) have multimodal support
- [ ] visual_extractor.py supports PDF, DOCX, PPTX, XLSX formats
- [ ] ingestion.py calls process_document_visuals for all formats
- [ ] rag_tools.py creates visual evidence items from image paths
- [ ] Evidence streaming infrastructure in chat.py

✅ **Environment Setup:**
- [ ] Qdrant running: `docker-compose up -d db_qdrant`
- [ ] Environment variables configured in `.env`
- [ ] OpenAI API key set (for GPT-4o)
- [ ] Required Python packages installed

## Step-by-Step Testing

### 1. Start Qdrant

```powershell
# In the workspace root
docker-compose up -d db_qdrant

# Verify Qdrant is running
docker-compose ps
# Should show: db_qdrant ... Up
```

### 2. Run End-to-End Validation

```powershell
# Activate virtual environment
cd c:\Users\HP\Placement Projects\SLT\ai_agents
.\.venv\Scripts\Activate.ps1

# Run validation script
python backend/test_visual_rag_e2e.py
```

**Expected Output:**
```
╔══════════════════════════════════════════════════════════════════════════╗
║             VISUAL RAG SYSTEM - END-TO-END VALIDATION                   ║
╚══════════════════════════════════════════════════════════════════════════╝

STEP 1: Validating Qdrant Collections
================================================================================
✓ Qdrant connected. Found X collections.

  Checking collection: askhr_docs
    └─ Total points: XXXX
    ├─ Visual record found:
    │  ├─ Image path: ./storage/visuals/doc_001_page_1.png
    │  ├─ Page: 1
    │  └─ Source: HR_Policy_2024.pdf

📊 Qdrant Summary:
   ├─ Collections found: 5
   ├─ Visual records found: 12
   └─ Records with image paths: 12

STEP 2: Testing RAG Retrieval with Visual Metadata
================================================================================
✓ Retrieval successful
   ├─ Context length: 2500 chars
   └─ Image paths returned: 2

STEP 3: Testing Evidence JSON Serialization
================================================================================
✓ Evidence JSON serialized successfully
   ├─ Items in sample: 2
   ├─ JSON length: 450 chars
   └─ Sample payload: {...}

STEP 4: Validating Extracted Image Files
================================================================================
✓ Storage directory exists: ./storage/visuals
   ├─ PNG files: 12
   └─ JSON metadata files: 12

   Total size: 8.45 MB

FINAL VALIDATION SUMMARY
================================================================================
  ✓  Qdrant collections exist
  ✓  Visual records in Qdrant (12)
  ✓  Records with image paths (12)
  ✓  RAG retrieval working (2500 chars)
  ✓  Image paths returned (2 paths)
  ✓  Evidence JSON serialization
  ✓  Storage directory exists (12 images)

✅ SYSTEM READY FOR UI TESTING
```

### 3. Ingest a Test Document with Visuals

**Option A: Through the API**
```powershell
# Prepare a document with charts/diagrams (PDF, DOCX, PPTX, or XLSX)
# Upload via the ingestion endpoint

$file = "C:\path\to\document_with_charts.pdf"
$uri = "http://localhost:8000/api/v1/ingest"

$form = @{
    file = Get-Item $file
    agent_id = "hr"
}

Invoke-WebRequest -Uri $uri -Method Post -Form $form -Verbose
```

**Option B: Check Existing Collections**
```powershell
# Query Qdrant directly
$qdrant_url = "http://localhost:6335"

# Get collection stats
Invoke-WebRequest -Uri "$qdrant_url/collections/askhr_docs" -Method Get | ConvertFrom-Json
```

### 4. Start Backend Server

```powershell
# In project root
python backend/main.py

# Expected output:
# INFO:     Started server process [XXXX]
# INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 5. Start Frontend Server

```powershell
# In a new terminal
cd frontend
npm run dev

# Expected output:
#   VITE v4.x.x  ready in XXX ms
#   ➜  Local:   http://localhost:3000/
```

### 6. Test Visual Queries in UI

Visit http://localhost:3000/ and test:

**Test Case 1: Flowchart Query**
- Query: "Show me the process flow diagram"
- Expected: Answer includes visual description + evidence image thumbnail

**Test Case 2: Organizational Chart**
- Query: "Display the organizational structure chart"
- Expected: Answer with diagram details + chart image

**Test Case 3: Table/Data Chart**
- Query: "What does the leave summary table show?"
- Expected: Answer with table context + table image

**Expected Behavior:**
1. Chat loads and responds
2. Answer includes relevant context
3. Evidence section shows image thumbnails (at bottom of answer)
4. Clicking image opens in full view
5. Multiple evidence items stacked if multiple visuals found

## Verification Checklist

- [ ] Backend starts without errors
- [ ] Frontend loads at localhost:3000
- [ ] Chat endpoint responds to queries
- [ ] Visual queries return meaningful answers
- [ ] Evidence images display in chat
- [ ] Evidence JSON properly formatted
- [ ] No 404 errors for image paths
- [ ] Images load correctly in frontend

## Troubleshooting

### Issue: "No visual records found in Qdrant"

**Solution:**
1. Ingest a document with charts/diagrams
2. Check ingestion logs for visual extraction
3. Verify `./storage/visuals/` directory has PNG files

```powershell
ls .\storage\visuals\  # Should show .png files
ls .\backend\storage\visuals\  # Check correct path
```

### Migrate Legacy Visual Records

After backing up Qdrant, move legacy visual assets into the served evidence
directory and backfill records with an unambiguous matching asset:

```powershell
cd backend
python scripts/migrate_visual_evidence.py
```

Records reported as `requires_reingestion` cannot be safely repaired from
metadata alone. Re-ingest their original source documents.

### Issue: Images not displaying in chat

**Solution:**
1. Check `/static/` endpoint is properly mounted in FastAPI
2. Verify image paths in evidence JSON are correct
3. Check browser console for 404 errors
4. Ensure `EVIDENCE_URL_PREFIX` in settings matches frontend expectations

```python
# In backend/core/config.py
EVIDENCE_URL_PREFIX = "/static/evidence"

# Frontend should request images from:
# http://localhost:8000/static/evidence/filename.png
```

### Issue: GPT-4o not analyzing images

**Solution:**
1. Verify `LLM_PROVIDER=openai` and `LLM_MODEL=gpt-4o` in `.env`
2. Check OpenAI API key is valid
3. Ensure API quota is not exceeded
4. Test with a simple image first

```powershell
# Test visual_extractor directly
python -c "
from services.visual_extractor import analyze_image_with_gpt4o
result = analyze_image_with_gpt4o('path/to/test.png')
print(result)
"
```

### Issue: Qdrant connection refused

**Solution:**
```powershell
# Ensure Qdrant is running
docker-compose up -d db_qdrant

# Verify connectivity
$qdrant_url = 'http://localhost:6335'
Invoke-WebRequest -Uri "$qdrant_url/health" -Method Get

# If not responding, restart Docker
docker-compose down
docker-compose up -d db_qdrant
docker-compose ps
```

## Implementation Details

### Data Flow: Document → Storage → Retrieval → Chat

```
┌─────────────────┐
│ Document Ingestion (ingestion.py)
│ └─ Calls process_document_visuals()
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Visual Extractor (visual_extractor.py)
│ ├─ PDF: PyMuPDF → render pages
│ ├─ DOCX: Extract embedded images
│ ├─ PPTX: Extract slide images
│ └─ XLSX: Extract chart images
│ ├─ GPT-4o analysis → description
│ └─ Save: PNG + JSON metadata
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────┐
│ Qdrant Storage (qdrant.py)
│ ├─ text chunks (type: "text")
│ └─ visual records (type: "visual_description")
│    └─ metadata.image_path = "./storage/visuals/..."
└────────┬─────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ RAG Retrieval (rag_tools.py)
│ ├─ search_knowledge_base()
│ ├─ Extract image_path from metadata
│ ├─ Return: {context, image_paths}
│ └─ _add_thread_evidence() → cache
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ KB Agents (kb_agent.py, etc.)
│ ├─ multimodal_check() node
│ ├─ encode_image_to_base64()
│ └─ GPT-4o with images
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Chat Router (chat.py)
│ ├─ Stream answer via SSE
│ ├─ consume_thread_evidence()
│ ├─ _build_evidence_stream_chunk()
│ └─ JSON: {"items": [...]}
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Frontend (React)
│ ├─ Parse evidence JSON
│ ├─ Display images from URLs
│ └─ Render in evidence section
└──────────────────────────────────┘
```

### Configuration Files

**`.env` - Environment Variables**
```
# Visual RAG
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# Qdrant
QDRANT_URL=http://localhost:6335

# Evidence Storage
EVIDENCE_URL_PREFIX=/static/evidence
EVIDENCE_STORAGE_DIR=./storage/evidence

# FastAPI
STATIC_FILES_DIR=./storage
```

**`docker-compose.yml` - Qdrant Setup**
```yaml
db_qdrant:
  image: qdrant/qdrant:latest
  ports:
    - "6335:6333"
  volumes:
    - ./data/qdrant:/qdrant/storage
  environment:
    QDRANT_API_KEY: ""
```

## Performance Considerations

### Image Size Optimization
- PNG files rendered at 150 DPI (balance quality vs size)
- Average visual per page: ~500KB
- Storage for 100 documents with 5 visuals each: ~250MB

### Token Usage
- GPT-4o analysis per image: ~200-500 tokens
- Estimated cost: $0.01-0.03 per image analysis

### Caching Strategy
- Evidence cached in thread-local storage during chat session
- Automatic cleanup after response generation
- Image files persisted in `./storage/visuals/`

## Monitoring & Logging

### Check Visual Extraction Logs
```powershell
# In backend terminal
# Look for lines like:
# INFO: Visual extraction: extracted 3 visuals from HR_Policy_2024.pdf
# INFO: Added visual record to Qdrant: doc_001_page_1
```

### Monitor Qdrant
```powershell
# Check collection stats
curl http://localhost:6335/collections/askhr_docs | jq

# Expected response shows:
# - points_count: total documents
# - status: "green"
```

### Track Evidence Streaming
```powershell
# Frontend console should show:
# EVIDENCE_JSON received
# Items parsed: 2
# Image URL: /static/evidence/doc_001_page_1.png
```

## Deployment Checklist

Before deploying to production:

- [ ] All visual extraction services tested
- [ ] Qdrant backup verified
- [ ] Image storage mounted on persistent volume
- [ ] Evidence URL rewriting configured for production domain
- [ ] API rate limits configured for GPT-4o calls
- [ ] Error handling for failed visual analysis
- [ ] Logging configured for debugging
- [ ] Performance tested with 1000+ documents

## Next Steps After Testing

1. **Monitor Performance**
   - Track GPT-4o API usage and costs
   - Monitor Qdrant query latency
   - Track image storage growth

2. **Optimize**
   - Adjust DPI setting for image quality/size tradeoff
   - Implement image compression
   - Cache GPT-4o responses

3. **Extend**
   - Add more document formats (SVG, Visio)
   - Implement multi-image summarization
   - Add visual search without text queries

## Support & Debugging

For issues or questions:

1. Check test output: `python backend/test_visual_rag_e2e.py`
2. Review backend logs for visual extraction errors
3. Inspect Qdrant collections: http://localhost:6335/
4. Check frontend console for image loading errors
5. Verify file permissions on `./storage/visuals/`

---

**Last Updated:** 2026-09-01  
**Version:** 1.0.0
