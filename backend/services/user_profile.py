"""Best-effort directory metadata, bound to the verified Microsoft account."""
from collections import OrderedDict
from dataclasses import replace
import hashlib
import logging
import time

import httpx

logger = logging.getLogger(__name__)
GRAPH_ME = "https://graph.microsoft.com/v1.0/me?$select=id,department,jobTitle"
_cache = OrderedDict()
_MAX_CACHE = 512


async def enrich_chat_identity(identity, user, graph_token):
    if not user or not isinstance(graph_token, str) or not graph_token or len(graph_token) > 16384:
        return identity
    key = (identity.principal, hashlib.sha256(graph_token.encode()).hexdigest())
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return replace(identity, **cached[1])
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            response = await client.get(GRAPH_ME, headers={"Authorization": f"Bearer {graph_token}"})
        if response.status_code != 200:
            logger.warning("Directory profile unavailable (HTTP %s).", response.status_code)
            return identity
        profile = response.json()
        if not isinstance(profile, dict) or not profile.get("id") or str(profile["id"]).lower() != str(user.get("oid", "")).lower():
            logger.warning("Directory profile did not match the verified account.")
            return identity
        def clean(value):
            return value.strip()[:200] or None if isinstance(value, str) else None
        fields = {"department": clean(profile.get("department")),
                  "job_title": clean(profile.get("jobTitle"))}
        _cache[key] = (time.monotonic() + 300, fields)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)
        return replace(identity, **fields)
    except (httpx.HTTPError, ValueError):
        # Never log the request headers, token or Graph response body.
        logger.warning("Directory profile lookup failed; continuing without enrichment.")
        return identity
