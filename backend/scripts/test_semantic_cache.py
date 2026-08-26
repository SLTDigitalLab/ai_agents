"""
test_semantic_cache.py — Manual integration test for the semantic cache.

Usage (run from project root with the backend venv activated):
    python backend/scripts/test_semantic_cache.py

What it does:
  1. Directly calls cache_set() to seed a question → answer pair.
  2. Calls cache_get() with the SAME question  → expects HIT.
  3. Calls cache_get() with a SYNONYM question  → expects HIT (semantic match).
  4. Calls cache_get() with an UNRELATED question → expects MISS.
  5. Calls cache_clear()                         → cleans up test data.
  6. Calls cache_get() again                     → expects MISS (cleared).
"""

import asyncio
import sys
import os

# Allow running from project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.semantic_cache import cache_get, cache_set, cache_clear, cache_stats


AGENT_ID = "__test_agent__"
ORIGINAL_QUESTION = "Do you have a car for rent?"
SYNONYM_QUESTION = "Is there an automobile available for rental?"
UNRELATED_QUESTION = "What is the latest interest rate set by the central bank?"
FAKE_ANSWER = (
    "Yes, we have several cars available for rent. "
    "You can choose from economy, sedan, and SUV categories. "
    "Please contact our rental desk for pricing and availability."
)


async def run_tests() -> None:
    print("=" * 60)
    print("  Semantic Cache Integration Test")
    print("=" * 60)

    # ── Stats before ──
    stats = await cache_stats(AGENT_ID)
    print(f"\n[STATS] Before seeding: {stats}")

    # ── 1. Seed the cache ──
    print(f"\n[1] Seeding cache with Q: {ORIGINAL_QUESTION!r}")
    await cache_set(ORIGINAL_QUESTION, FAKE_ANSWER, AGENT_ID)
    print("    Done.")

    stats = await cache_stats(AGENT_ID)
    print(f"[STATS] After seeding: entries={stats.get('entry_count')}")

    # ── 2. Exact same question ──
    print(f"\n[2] GET with SAME question: {ORIGINAL_QUESTION!r}")
    result = await cache_get(ORIGINAL_QUESTION, AGENT_ID)
    if result:
        print(f"    ✅  HIT (expected) — answer[:80]: {result[:80]!r}")
    else:
        print("    ❌  MISS (unexpected) — exact question should hit!")

    # ── 3. Synonym question ──
    print(f"\n[3] GET with SYNONYM question: {SYNONYM_QUESTION!r}")
    result = await cache_get(SYNONYM_QUESTION, AGENT_ID)
    if result:
        print(f"    ✅  HIT (expected) — synonyms recognised as same meaning.")
        print(f"    Answer[:80]: {result[:80]!r}")
    else:
        print(
            "    ⚠️   MISS — try lowering SEMANTIC_CACHE_THRESHOLD in .env "
            "(current value is probably fine; embedding models may differ)."
        )

    # ── 4. Unrelated question ──
    print(f"\n[4] GET with UNRELATED question: {UNRELATED_QUESTION!r}")
    result = await cache_get(UNRELATED_QUESTION, AGENT_ID)
    if result is None:
        print("    ✅  MISS (expected) — unrelated question correctly not cached.")
    else:
        print(
            f"    ⚠️   HIT (unexpected) — similarity threshold may be too low. "
            f"Consider raising SEMANTIC_CACHE_THRESHOLD."
        )

    # ── 5. Clear test data ──
    print(f"\n[5] Clearing cache for agent '{AGENT_ID}'…")
    deleted = await cache_clear(AGENT_ID)
    print(f"    Deleted {deleted} entries.")

    # ── 6. Confirm cleared ──
    print(f"\n[6] GET after clear: {ORIGINAL_QUESTION!r}")
    result = await cache_get(ORIGINAL_QUESTION, AGENT_ID)
    if result is None:
        print("    ✅  MISS (expected) — cache cleared successfully.")
    else:
        print("    ❌  HIT (unexpected) — cache was not cleared!")

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())
