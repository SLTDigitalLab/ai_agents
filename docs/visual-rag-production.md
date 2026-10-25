# Visual RAG production release

Production base: `merge/3d-character-to-monitoringpulse` at `b8e2752`.
Replacement pipeline source: `voice-test-visual-rag-merge` at `0a6c0f0`.
Relevance fixes: `feature/visual-rag-relevance-filter` at `d3fd7e8`, merged to
`voice-test` by `e16f4f9`.

This integration ports the replacement pipeline to the current production
architecture. It does not merge unrelated staging changes.

## How it works

OneDrive and SharePoint ingestion now run the staging vision extractor. It
describes diagrams, charts, and other meaningful visuals and stores separate
`visual_description` documents in the same Qdrant collection as the text.
Images are stored under `EVIDENCE_STORAGE_DIR`, which is already mounted by the
production Compose backend. The frontend can force re-ingestion of unchanged
OneDrive files; SharePoint already has that control.

Retrieval searches those descriptions, including a dedicated search for
explicit diagram requests. It checks raw dense cosine similarity and asks the
configured chat model whether each description directly answers the question.
Only approved visuals appear beneath the answer. The previous PDF crop evidence
on text chunks is ignored while `VISUAL_RAG_ENABLED=true`.

**Existing files must be re-ingested to create visual descriptions.** A code
deployment alone will not make images from old Qdrant points appear. Vision
extraction uses OpenAI `gpt-4o`, so the production backend needs a valid
`OPENAI_API_KEY`. Re-ingestion uses model calls and should begin with one known
document before a full folder.

## Before deployment

1. On the VM, record `git rev-parse HEAD`, `git status --short`, the backend
   image ID, and `docker compose -f docker-compose.prod.yml ps`. Confirm the
   current production checkout and that it is clean.
2. Confirm `OPENAI_API_KEY`, the configured chat and embedding providers, and
   the evidence volume are available. Confirm `KB_REMOTE_URL` is unset for
   agents expected to use the local production Qdrant index. Do not print or
   commit secret values.
3. Before forcing any re-ingestion, take a restorable snapshot of the affected
   Qdrant collection and preserve the evidence directory. Re-ingestion replaces
   existing points for each file, so Git alone cannot restore the old index.
4. Merge the reviewed integration into the production branch only if its
   production base still matches. Keep the VM's actual previous commit SHA.

## Deploy and validate

Rebuild the backend and frontend from the merged checkout:

```sh
docker compose -f docker-compose.prod.yml up -d --build backend frontend
docker compose -f docker-compose.prod.yml ps backend frontend
docker compose -f docker-compose.prod.yml logs --tail 100 backend
```

First use a small folder containing one representative visual document and
force re-ingest it through the admin panel. Verify ingestion creates
`visual_description` Qdrant points and image
files in the evidence directory. Then test, with a signed-in account:

1. A question about that document's diagram shows the correct image and cites
   the correct source.
2. A different-topic question does not show that image.
3. A text-only question still answers with citations.
4. Chat ownership, admin ingestion, and the live voice flow still work.

Only after that small check should an admin force re-ingest the remaining
relevant folders. Preserve PostgreSQL and Qdrant volumes throughout.

## Rollback

Set `VISUAL_RAG_ENABLED=false` in the server `.env` and recreate the backend
to stop using new visual records. Text answers remain available; images from
files re-ingested under this release may be absent because their old crop
metadata was replaced. For a full rollback, restore the VM's recorded
pre-deployment commit, rebuild backend and frontend, and restore the affected
Qdrant collection snapshot plus evidence files taken before re-ingestion. The
published safety branch
`backup/prod-before-visual-rag-2026-10-25` points to `b8e2752`, but use the
VM's verified prior SHA if it differs. Keep `.env`, the evidence directory, and
database volumes intact.
