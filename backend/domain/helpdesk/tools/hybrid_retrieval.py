"""
Hybrid Retrieval + Candidate Reranker stage for helpdesk category
classification — merges domain.helpdesk.tools.category_kb_tools (75
hand-written category rows, guaranteed one row per category — a coverage
floor even for rarely-seen categories) with
domain.helpdesk.tools.ticket_example_tools (~13k real past tickets —
better phrasing match, but only for patterns already seen before).

Rationale: the two sources have complementary blind spots. The KB can't
match unusual real-world phrasing precisely; the real-ticket corpus has no
fallback when an incoming message doesn't resemble anything seen before.
Querying both and merging closes each source's gap with the other, rather
than picking one and inheriting its specific weakness.

Each source's own vector-search score (already a hybrid dense+BM25 fusion
score from its own Qdrant collection — see search_category_candidates /
search_ticket_examples) is combined with a keyword-overlap score against
the merged candidate's full text (name + description + symptoms/example
text), so ranking isn't purely a similarity artifact from one embedding
call. A category retrieved by BOTH sources gets a small reinforcement
bonus, since independent agreement from two different corpora is a
stronger signal than either alone.
"""

import asyncio
import logging
import re

from domain.helpdesk.tools.category_kb_tools import search_category_candidates
from domain.helpdesk.tools.ticket_example_tools import search_ticket_examples

log = logging.getLogger(__name__)

# Weights for the final blended rerank score. Vector similarity still
# dominates (it's the strongest available signal), keyword overlap is a
# secondary tiebreaker/sanity check, and the multi-source bonus rewards
# candidates independently corroborated by both the KB and real tickets.
_VECTOR_WEIGHT = 0.65
_KEYWORD_WEIGHT = 0.25
_MULTI_SOURCE_BONUS = 0.10


def _tokenize(text: str) -> set[str]:
    return set(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _keyword_overlap_score(query_words: set[str], candidate_text: str) -> float:
    """Fraction of the query's own words that appear somewhere in the
    candidate's text. Recall-oriented (denominator is the query, not the
    candidate) since candidate text is much longer than a typical ticket
    message and a symmetric/Jaccard score would be dominated by that
    length mismatch."""
    if not query_words:
        return 0.0
    candidate_words = _tokenize(candidate_text)
    if not candidate_words:
        return 0.0
    return len(query_words & candidate_words) / len(query_words)


async def search_hybrid_candidates(
    query: str, k: int = 5, k_kb: int = 5, k_examples: int = 5
) -> list[dict]:
    """Query both the category KB and the real-ticket-examples corpus
    concurrently, merge candidates that name the same (main_category,
    sub_category), rerank the merged pool by a blended vector+keyword+
    multi-source-agreement score, and return the top-k.

    Each returned dict carries: main_category, sub_category, description,
    customer_expressions, symptoms, similar_categories, do_not_use_when
    (from the KB side, when present), example_text (a representative real
    ticket, from the examples side, when present), source ("kb",
    "example", or "kb+example"), and _score (the final blended rerank
    score, 0-1ish, comparable across candidates regardless of source).

    Never raises — an empty list from either or both underlying searches
    just means a smaller (or empty) merged pool, same fallback contract as
    the two functions this wraps.
    """
    kb_candidates, example_candidates = await asyncio.gather(
        search_category_candidates(query, k=k_kb),
        search_ticket_examples(query, k=k_examples),
    )

    merged: dict[tuple[str, str], dict] = {}

    for c in kb_candidates:
        main_category = c.get("main_category", "")
        sub_category = c.get("sub_category", "")
        if not main_category:
            continue
        key = (main_category, sub_category)
        entry = merged.setdefault(
            key,
            {
                "main_category": main_category,
                "sub_category": sub_category,
                "sources": set(),
                "kb_score": 0.0,
                "example_score": 0.0,
                "description": "",
                "customer_expressions": "",
                "symptoms": "",
                "similar_categories": "",
                "do_not_use_when": "",
                "example_text": "",
            },
        )
        entry["sources"].add("kb")
        entry["kb_score"] = max(entry["kb_score"], float(c.get("_score", 0.0) or 0.0))
        entry["description"] = entry["description"] or c.get("description", "") or ""
        entry["customer_expressions"] = entry["customer_expressions"] or c.get("customer_expressions", "") or ""
        entry["symptoms"] = entry["symptoms"] or c.get("symptoms", "") or ""
        entry["similar_categories"] = entry["similar_categories"] or c.get("similar_categories", "") or ""
        entry["do_not_use_when"] = entry["do_not_use_when"] or c.get("do_not_use_when", "") or ""

    for c in example_candidates:
        main_category = c.get("main_category", "")
        sub_category = c.get("sub_category", "")
        if not main_category:
            continue
        key = (main_category, sub_category)
        entry = merged.setdefault(
            key,
            {
                "main_category": main_category,
                "sub_category": sub_category,
                "sources": set(),
                "kb_score": 0.0,
                "example_score": 0.0,
                "description": "",
                "customer_expressions": "",
                "symptoms": "",
                "similar_categories": "",
                "do_not_use_when": "",
                "example_text": "",
            },
        )
        entry["sources"].add("example")
        entry["example_score"] = max(entry["example_score"], float(c.get("_score", 0.0) or 0.0))
        if not entry["example_text"]:
            entry["example_text"] = c.get("example_text", "") or ""

    query_words = _tokenize(query)
    candidates = list(merged.values())
    for entry in candidates:
        vector_score = max(entry["kb_score"], entry["example_score"])
        combined_text = " ".join(
            filter(
                None,
                [
                    entry["main_category"],
                    entry["sub_category"],
                    entry["description"],
                    entry["customer_expressions"],
                    entry["symptoms"],
                    entry["example_text"],
                ],
            )
        )
        keyword_score = _keyword_overlap_score(query_words, combined_text)
        multi_source = _MULTI_SOURCE_BONUS if len(entry["sources"]) > 1 else 0.0
        entry["_score"] = min(
            1.0, _VECTOR_WEIGHT * vector_score + _KEYWORD_WEIGHT * keyword_score + multi_source
        )
        entry["source"] = "+".join(sorted(entry["sources"]))
        del entry["sources"]

    candidates.sort(key=lambda e: e["_score"], reverse=True)

    log.info(
        "Hybrid retrieval: %d KB + %d example candidate(s) -> %d merged, returning top %d for query=%r",
        len(kb_candidates),
        len(example_candidates),
        len(candidates),
        k,
        query,
    )
    return candidates[:k]
