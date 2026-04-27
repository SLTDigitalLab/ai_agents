"""
Shared cache constants.

We keep Redis (exact/fuzzy) and Qdrant (semantic) caches aligned to avoid serving
stale responses from one layer after the other has expired.
"""

# 24 hours (same as the Redis exact cache TTL)
CACHE_TTL_SECONDS: int = 86400

