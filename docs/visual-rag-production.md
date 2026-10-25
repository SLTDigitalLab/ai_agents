# Visual RAG relevance filter: production release

Prepared from production branch `merge/3d-character-to-monitoringpulse` at
`b8e2752` on 2026-10-25. The source feature was merged into `voice-test` by
`e16f4f9` (feature commit `d3fd7e8`). Staging and production have diverged, so
this release ports the image relevance checks to production's existing PDF
evidence pipeline. It does not merge the rest of staging.

## Behavior

- The existing ingestion process stores PDF image crops on Qdrant text chunks.
- Retrieval checks each candidate chunk's raw dense cosine similarity against
  the question, then asks the configured chat model whether its passage directly
  helps answer the question. Only approved images enter the evidence stream.
- Existing chat answer, citations, authentication, and frontend image rendering
  remain in place. A review failure omits images but still returns the text answer.
- Existing indexed documents work without migration or re-ingestion. The review
  uses text from the crop's page, so final image relevance still needs a live
  check against representative uploaded files.

## Before deployment

1. On the production VM, record `git rev-parse HEAD`, `git status --short`,
   `docker compose -f docker-compose.prod.yml ps`, and the current backend
   image ID. Verify the checkout is the intended production branch and clean.
2. Merge the reviewed integration into that branch only after confirming its
   current tip. Keep the prior commit SHA for a full rollback.
3. Preserve `.env`, the evidence directory, PostgreSQL, and Qdrant volumes.
   No database migration is needed.

## Deploy and smoke test

Rebuild only the backend service from the merged production checkout:

```sh
docker compose -f docker-compose.prod.yml up -d --build backend
docker compose -f docker-compose.prod.yml ps backend
docker compose -f docker-compose.prod.yml logs --tail 100 backend
```

Using a signed-in account and documents already present in production, check:

1. A question directly about a known diagram shows its image below the answer
   and cites the correct document.
2. A question about a different topic does not show that diagram.
3. A normal text-only question still answers with its expected citation.
4. An unrelated answer, private chat, and admin ingestion still behave normally.

## Rollback

For a quick image-behavior rollback, set
`VISUAL_EVIDENCE_RELEVANCE_ENABLED=false` in the server `.env` and recreate the
backend service using the command above. This restores the earlier image
selection behavior without changing indexed documents.

For a full code rollback, restore the recorded pre-deployment production commit
and rebuild the backend service. The local safety branch
`backup/prod-before-visual-rag-2026-10-25` points to `b8e2752`, but the VM's
actual pre-deployment commit must be verified before using that SHA. Do not
remove or reset the database or evidence volumes.
