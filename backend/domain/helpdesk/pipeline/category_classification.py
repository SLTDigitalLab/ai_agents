"""
CATEGORY PREDICTION PIPELINE — every classify_ticket_category*() variant
used to turn a user's issue text into a (main_category, sub_category) pair,
plus the shared validation/formatting helpers they all rely on.

classify_ticket_category_pipeline() is the one actually wired into the live
graph (called from ticket_draft.draft_ticket() / category_clarification_
handler()): Hybrid Retrieval (domain/helpdesk/tools/hybrid_retrieval.py) ->
Hierarchical LLM Classifier (_classify_hierarchical, two stages: main
category then sub-category) -> self-consistency Confidence Check (vote
across self_consistency_n independent passes).

Every other classify_ticket_category_*() function here — classify_ticket_
category() (full-list prompt baseline), classify_ticket_category_vector()
(single-shot vector retrieval, the classifier live traffic used before
2026-08-09), classify_ticket_category_finetuned()/_finetuned_vector(), and
classify_ticket_category_examples() (real-ticket retrieval) — is [EVAL-ONLY]:
kept for domain/helpdesk/scripts/run_accuracy_eval.py's A/B comparison and as a
rollback path, not called from the live conversational graph. See
helpdesk-category-accuracy-gap project memory for the accuracy numbers
behind the 2026-08-09 swap to the pipeline classifier.

All classifiers converge on _resolve_category() to snap the LLM's proposed
category text onto a real row from the authoritative Postgres category
list — the LLM's own wording is never trusted as-is.
"""

import asyncio
import re
from collections import Counter
from functools import lru_cache

from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage

from core.config import settings
from services.helpdesk_tickets import list_categories
from domain.helpdesk.pipeline.helpers import llm, INTERNAL_LLM_TAG, _message_to_text
from domain.helpdesk.prompts import (
    draft_ticket_system_prompt,
    category_vector_system_prompt,
    category_examples_system_prompt,
    category_hierarchical_main_system_prompt,
    category_hierarchical_sub_system_prompt,
    category_finetuned_system_prompt,
)
from domain.helpdesk.tools.category_kb_tools import search_category_candidates
from domain.helpdesk.tools.ticket_example_tools import search_ticket_examples
from domain.helpdesk.tools.hybrid_retrieval import search_hybrid_candidates
from domain.helpdesk.tools.helpdesk_tools import category_llm, list_categories_tool

# Confidence-Check stage (see ticket_draft.draft_ticket() below): below this
# self-consistency agreement fraction, ask the user one clarifying
# question instead of drafting on a guess. Matches the threshold used to
# measure "low-confidence rate" in domain/helpdesk/scripts/run_pipeline_eval.py
# (catches "not all 3 self-consistency passes agreed" at n=3: confidence
# 1/3 or 2/3, not just 1/3).
_CATEGORY_CONFIDENCE_THRESHOLD = 0.67

# The three categories that describe the same real-world event (an order
# stuck somewhere after submission) from three different backend systems'
# point of view — see helpdesk-category-accuracy-gap project memory. A
# customer can't tell you which backend system currently owns their order,
# so when a low-confidence disagreement is BETWEEN these three, asking a
# clarifying question wastes a turn instead of resolving anything —
# ticket_draft.draft_ticket() skips straight to drafting for this specific
# case.
_AMBIGUOUS_CATEGORY_CLUSTER = {
    "SOA",
    "CRM OM - After Submit Issues",
    "Clarity OSS - Order Issues",
}


# [HELPER] Regex-extracts "Category: X" / "Sub-category: Y" out of an LLM
# reply. Used by every classify_ticket_category* variant below.
def _parse_category_draft(reply: str) -> tuple[str, str]:
    """Extract the **Category:** / **Sub-category:** values draft_ticket_system_prompt
    instructs the LLM to emit in its reply."""
    main_category = ""
    sub_category = ""
    cat_match = re.search(
        r"\*{0,2}Category\*{0,2}\s*[:\|]\s*\*{0,2}([^\n\|*]+)",
        reply,
        re.IGNORECASE,
    )
    sub_match = re.search(
        r"\*{0,2}Sub-?category\*{0,2}\s*[:\|]\s*\*{0,2}([^\n\|*]+)",
        reply,
        re.IGNORECASE,
    )
    if cat_match:
        main_category = cat_match.group(1).strip()
    if sub_match:
        sub_category = sub_match.group(1).strip()
    return main_category, sub_category


# [HELPER] Snaps an LLM-proposed (main_category, sub_category) pair onto a
# real row from the authoritative Postgres category list — the LLM's own
# wording is never trusted as-is. Tries exact match, then same-category
# partial/keyword match, then global partial/keyword match, in that order.
def _resolve_category(
    main_category: str,
    sub_category: str,
    original_query: str,
    valid_categories: list[tuple[str, str, str, str]],
) -> tuple[str, str]:
    """Validate an LLM-proposed category/subcategory pair against the
    authoritative DB list, using exact → partial → keyword-overlap fallback
    strategies, so callers always end up with a real DB category. Keyword
    overlap also weighs each category's description and mined keywords, not
    just its name, since two subcategories can share generic wording but
    describe different scopes. Returns the LLM's own text unchanged if
    valid_categories is empty."""
    if not valid_categories:
        return main_category, sub_category

    matched: tuple[str, str, str, str] | None = None

    # 1. Exact case-insensitive match on BOTH category_name and subcategory.
    #    Must be checked as a pair first — matching category_name alone would
    #    grab whichever subcategory happens to sort first under that name,
    #    silently swapping out the subcategory the LLM actually chose (and
    #    that the user already saw in the draft).
    matched = next(
        (
            (cn, sc, desc, kw)
            for cn, sc, desc, kw in valid_categories
            if cn.lower() == main_category.lower()
            and sc.lower() == sub_category.lower()
        ),
        None,
    )

    # 2. category_name matches exactly, but subcategory text doesn't — stay
    #    within that category_name's own subcategories rather than falling
    #    through to an unrelated category.
    if not matched and main_category:
        same_category = [
            (cn, sc, desc, kw)
            for cn, sc, desc, kw in valid_categories
            if cn.lower() == main_category.lower()
        ]
        if same_category:
            # 2a. Partial/substring match on subcategory text.
            matched = next(
                (
                    (cn, sc, desc, kw)
                    for cn, sc, desc, kw in same_category
                    if sub_category
                    and (
                        sc.lower() in sub_category.lower()
                        or sub_category.lower() in sc.lower()
                    )
                ),
                None,
            )
            # 2b. No subcategory text match — pick the subcategory with the
            #     most keyword overlap against the user's issue, scored over
            #     the subcategory name, its description, and its mined
            #     keywords, instead of defaulting to whichever row sorts
            #     first.
            if not matched:
                query_words = set(
                    re.sub(r"[^\w\s]", "", original_query.lower()).split()
                )
                best_score = -1
                for cn, sc, desc, kw in same_category:
                    sub_words = set(
                        re.sub(
                            r"[^\w\s]", "", (sc + " " + desc + " " + kw).lower()
                        ).split()
                    )
                    score = len(query_words & sub_words)
                    if score > best_score:
                        best_score = score
                        matched = (cn, sc, desc, kw)

    # 3. Partial string match on category_name (category_name itself wasn't
    #    an exact hit — e.g. LLM said "Networking" for DB's "Network").
    if not matched:
        matched = next(
            (
                (cn, sc, desc, kw)
                for cn, sc, desc, kw in valid_categories
                if main_category
                and (
                    cn.lower() in main_category.lower()
                    or main_category.lower() in cn.lower()
                )
            ),
            None,
        )
    # 4. Keyword overlap between issue text and category name + subcategory +
    #    description + mined keywords (last resort).
    if not matched:
        query_words = set(re.sub(r"[^\w\s]", "", original_query.lower()).split())
        best_score = -1
        for cn, sc, desc, kw in valid_categories:
            cat_words = set(
                re.sub(
                    r"[^\w\s]", "", (cn + " " + sc + " " + desc + " " + kw).lower()
                ).split()
            )
            score = len(query_words & cat_words)
            if score > best_score:
                best_score = score
                matched = (cn, sc, desc, kw)

    return (matched[0], matched[1]) if matched else (main_category, sub_category)


# [EVAL-ONLY] Original prompt-based classifier: shows the LLM the full
# category list via list_categories_tool. ticket_draft.draft_ticket() no
# longer calls this live — see classify_ticket_category_pipeline() for the
# current one (classify_ticket_category_vector() below is the previous live
# classifier, now eval-only too).
async def classify_ticket_category(message: str) -> tuple[str, str]:
    """Run the same category classification draft_ticket() uses, standalone —
    for batch accuracy-evaluation scripts. Not part of the conversational
    graph: makes its own two-turn tool call directly and returns the
    resolved (main_category, sub_category) without touching graph state or
    creating a ticket.
    """
    system_prompt = {
        "role": "system",
        "content": draft_ticket_system_prompt(message, "EVAL", continuation=False),
    }

    turn1 = await category_llm.ainvoke(
        [system_prompt], config={"tags": [INTERNAL_LLM_TAG]}
    )
    tool_calls = getattr(turn1, "tool_calls", None) or []
    if not tool_calls:
        print("[classify_ticket_category] LLM skipped the list_categories_tool call")
        return "", ""

    tool_call = tool_calls[0]
    tool_result = list_categories_tool.invoke(tool_call["args"])
    tool_message = ToolMessage(content=tool_result, tool_call_id=tool_call["id"])

    turn2 = await category_llm.ainvoke(
        [system_prompt, turn1, tool_message], config={"tags": [INTERNAL_LLM_TAG]}
    )
    reply = _message_to_text(turn2)
    main_category, sub_category = _parse_category_draft(reply)

    try:
        all_categories = list_categories()
    except Exception as exc:
        print(f"[classify_ticket_category] list_categories() error: {exc}")
        all_categories = []

    valid_categories: list[tuple[str, str, str, str]] = [
        (
            c.get("category_name", ""),
            c.get("subcategory", ""),
            c.get("description", "") or "",
            c.get("keywords", "") or "",
        )
        for c in all_categories
        if c.get("category_name")
    ]

    return _resolve_category(main_category, sub_category, message, valid_categories)


# [HELPER] Renders search_category_candidates() vector-search results as
# numbered text blocks for the LLM prompt in classify_ticket_category_vector().
def _format_category_candidates(candidates: list[dict]) -> str:
    """Render search_category_candidates() results as numbered blocks for
    category_vector_system_prompt(). Only fields useful for disambiguation
    are surfaced — raw vector scores are omitted from the LLM-facing text."""
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        lines = [
            f"{i}. Category: {c.get('main_category', 'N/A')} | "
            f"Sub-category: {c.get('sub_category', 'N/A')}",
        ]
        if c.get("description"):
            lines.append(f"   Description: {c['description']}")
        if c.get("customer_expressions"):
            lines.append(f"   Customer expressions: {c['customer_expressions']}")
        if c.get("symptoms"):
            lines.append(f"   Symptoms: {c['symptoms']}")
        if c.get("similar_categories"):
            lines.append(f"   Similar categories (don't confuse with): {c['similar_categories']}")
        if c.get("do_not_use_when"):
            lines.append(f"   Do not use when: {c['do_not_use_when']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# [EVAL-ONLY as of 2026-08-09] Formerly THE LIVE CLASSIFIER — draft_ticket()
# called this until it was swapped for classify_ticket_category_pipeline()
# (measured +8.0 points real-traffic accuracy, see that function's
# docstring). Kept as-is for run_accuracy_eval.py --method vector A/B
# comparison and as a fast rollback path.
# Retrieves top-k similar categories from the category-KB Qdrant collection,
# asks the LLM to pick the best match from just those candidates, then
# validates the pick against Postgres via _resolve_category().
async def classify_ticket_category_vector(message: str, k: int = 5) -> tuple[str, str]:
    """Vector-retrieval variant of classify_ticket_category(), for accuracy
    A/B comparison (see domain/helpdesk/scripts/run_accuracy_eval.py --method
    vector). Standalone from draft_ticket()'s live flow.

    Instead of prompting the LLM with the full category list via
    list_categories_tool, this retrieves the top-k most similar categories
    from the category knowledge base Qdrant collection (ingested by
    domain/helpdesk/scripts/ingest_category_kb.py) and asks the LLM to pick the
    best match from just those candidates. The final answer is still
    validated against the authoritative Postgres category list via
    _resolve_category(), exactly like the prompt-based classifier.

    k defaults to 5 but is caller-configurable (see run_accuracy_eval.py
    --top-k) — short, keyword-poor ticket messages carry little semantic
    signal, so a wider candidate net trades away some of the context-size
    win for better recall of the true category.
    """
    candidates = await search_category_candidates(message, k=k)
    valid_categories = _load_valid_categories("classify_ticket_category_vector")

    if not candidates:
        print("[classify_ticket_category_vector] no candidates returned from vector search")
        return _resolve_category("", "", message, valid_categories)

    candidates_text = _format_category_candidates(candidates)
    system_prompt = {
        "role": "system",
        "content": category_vector_system_prompt(message, candidates_text),
    }

    response = await llm.ainvoke([system_prompt], config={"tags": [INTERNAL_LLM_TAG]})
    reply = _message_to_text(response)
    main_category, sub_category = _parse_category_draft(reply)

    return _resolve_category(main_category, sub_category, message, valid_categories)


# [HELPER] Renders search_hybrid_candidates() results as numbered text
# blocks, tagging each candidate's source (kb / example / kb+example) so
# the LLM can weigh a real-ticket-backed candidate differently from a
# KB-only one. Used by _classify_hierarchical()'s stage-2 (sub-category)
# prompt; stage 1 (main-category) uses its own coarser summary instead.
def _format_hybrid_candidates(candidates: list[dict]) -> str:
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        lines = [
            f"{i}. Category: {c.get('main_category', 'N/A')} | "
            f"Sub-category: {c.get('sub_category', 'N/A')} | "
            f"Source: {c.get('source', 'N/A')}",
        ]
        if c.get("description"):
            lines.append(f"   Description: {c['description']}")
        if c.get("customer_expressions"):
            lines.append(f"   Customer expressions: {c['customer_expressions']}")
        if c.get("symptoms"):
            lines.append(f"   Symptoms: {c['symptoms']}")
        if c.get("example_text"):
            lines.append(f"   Real ticket example: {c['example_text']}")
        if c.get("similar_categories"):
            lines.append(f"   Similar categories (don't confuse with): {c['similar_categories']}")
        if c.get("do_not_use_when"):
            lines.append(f"   Do not use when: {c['do_not_use_when']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# [HELPER] Single pass of the two-step Hierarchical LLM Classifier stage:
# pick the main category first (from the distinct main categories present
# in the hybrid-retrieval candidate pool), then pick the sub-category
# scoped to only that main category's candidates. Two LLM calls instead of
# classify_ticket_category_vector()'s one — see
# classify_ticket_category_pipeline() for why (self-consistency needs a
# clean, resolved (main, sub) pair per pass to vote over) and for the
# design note on why this still doesn't resolve the SOA / CRM OM - After
# Submit Issues / Clarity OSS confusion (see helpdesk-category-accuracy-gap
# memory): that ambiguity is BETWEEN main categories, and stage 1 has to
# pick one main category, so a hierarchical split doesn't sidestep it —
# it's still one guess among ~3 confusable options, just made explicitly
# instead of implicitly.
async def _classify_hierarchical(
    message: str,
    candidates: list[dict],
    valid_categories: list[tuple[str, str, str, str]],
    llm_client=None,
) -> tuple[str, str]:
    """llm_client defaults to the module's shared deterministic `llm`
    (temperature=0, see helpers.py). classify_ticket_category_pipeline()
    passes a higher-temperature client instead when self_consistency_n > 1,
    so the multiple passes can actually disagree with each other.

    Both ainvoke() calls below pass config={"tags": [INTERNAL_LLM_TAG]} —
    without it, routers/chat.py's astream_events listener treats these
    internal, never-user-facing "**Category:** X" replies exactly like any
    other LLM call in the graph and streams their raw tokens straight to
    the user. With self_consistency_n>1 running several of these calls
    CONCURRENTLY (asyncio.gather in classify_ticket_category_pipeline),
    their tokens interleave into unreadable garbage ahead of the real,
    final draft reply — observed live 2026-08-11 as e.g. "****Category
    Category:** CRM SR & TT:**** Issues CRM...". routers/chat.py's
    SUPPRESS_STREAM_NODES can't fix this by node name alone, since these
    calls run inside the SAME graph node (ticket_draft.draft_ticket /
    category_clarification_handler) as the legitimate final streaming
    reply — only a tag lets chat.py tell them apart within one node."""
    client = llm_client or llm

    # Best-scoring candidate per distinct main category, ranked by that score.
    best_by_main: dict[str, dict] = {}
    for c in candidates:
        mc = c["main_category"]
        if mc not in best_by_main or c["_score"] > best_by_main[mc]["_score"]:
            best_by_main[mc] = c
    main_ranked = sorted(best_by_main.values(), key=lambda c: c["_score"], reverse=True)

    if len(main_ranked) == 1:
        chosen_main = main_ranked[0]["main_category"]
    else:
        main_candidates_text = "\n\n".join(
            f"{i}. {c['main_category']} "
            f"(best-matching sub-category seen: \"{c['sub_category']}\", source: {c['source']})\n"
            f"   {c.get('description') or c.get('example_text') or ''}"
            for i, c in enumerate(main_ranked, start=1)
        )
        response = await client.ainvoke(
            [
                {
                    "role": "system",
                    "content": category_hierarchical_main_system_prompt(message, main_candidates_text),
                }
            ],
            config={"tags": [INTERNAL_LLM_TAG]},
        )
        reply = _message_to_text(response)
        raw_main, _ = _parse_category_draft(reply)
        chosen_main = raw_main if raw_main in best_by_main else main_ranked[0]["main_category"]

    sub_candidates = [c for c in candidates if c["main_category"] == chosen_main]
    if len(sub_candidates) == 1:
        chosen_sub = sub_candidates[0]["sub_category"]
    else:
        sub_candidates_text = _format_hybrid_candidates(sub_candidates)
        response = await client.ainvoke(
            [
                {
                    "role": "system",
                    "content": category_hierarchical_sub_system_prompt(
                        message, chosen_main, sub_candidates_text
                    ),
                }
            ],
            config={"tags": [INTERNAL_LLM_TAG]},
        )
        reply = _message_to_text(response)
        _, chosen_sub = _parse_category_draft(reply)

    return _resolve_category(chosen_main, chosen_sub, message, valid_categories)


# [LIVE — swapped in 2026-08-09] Full "Hybrid Retrieval -> Candidate
# Reranker -> Top-k -> Hierarchical LLM Classifier -> Confidence Check"
# pipeline. Called from ticket_draft.draft_ticket() / category_
# clarification_handler() — see this module's docstring for the accuracy
# numbers behind the swap from classify_ticket_category_vector().
#
# Confidence is measured by self-consistency: run the hierarchical
# classifier self_consistency_n times (temperature > 0 on a dedicated
# sampling client, see _get_sampling_llm) and take the majority (main, sub)
# vote; confidence is that majority's vote share. This directly targets the
# "selection gap" found in Helpdesk_Prediction_Detail_Report.pdf
# (recall@5 65% vs. final accuracy 34.5% on representative_eval_200.xlsx) —
# cases where the right candidate was on the table but a single LLM pass
# picked a different one — by letting multiple independent passes outvote a
# one-off wrong pick, AND by surfacing (via the returned confidence) which
# tickets a live caller should route to a clarification turn instead of
# trusting outright.
async def classify_ticket_category_pipeline(
    message: str, k: int = 5, self_consistency_n: int = 3
) -> tuple[str, str, float]:
    """Returns (main_category, sub_category, confidence). confidence is
    the fraction of self_consistency_n independent hierarchical-classifier
    passes that agreed with the returned (main_category, sub_category)
    pair — 1.0 means every pass agreed, 1/self_consistency_n means every
    pass disagreed with every other (the plurality winner among all-
    distinct picks).

    Never raises. This is the most expensive, most external-dependency-
    heavy step in the whole ticket-creation chain (an embedding+Qdrant
    search plus up to 2 * self_consistency_n LLM calls) and, unlike every
    other external call in this file (search_hybrid_candidates() itself,
    _load_valid_categories()'s list_categories() call, etc.), it used to
    have no try/except of its own — one failed LLM call (timeout, a
    provider-side content-filter rejection on unusual-looking text like an
    account/order number, ...) would raise straight out of draft_ticket()/
    category_clarification_handler(), past chat.py's outer handler, killing
    the ticket-creation turn right after kb_search_agent's "not found, let
    me help you create a ticket" reply had already streamed — a real
    conversation observed live 2026-09-10 stopping exactly there, with no
    ticket draft ever following. Falls back to the same "no candidates"
    keyword-overlap resolution already used below when retrieval itself
    comes back empty, so a failure here degrades to a low-confidence
    fallback category (triggering draft_ticket()'s clarification-question
    path) instead of ending the conversation."""
    valid_categories = _load_valid_categories("classify_ticket_category_pipeline")

    try:
        candidates = await search_hybrid_candidates(message, k=k)

        if not candidates:
            print("[classify_ticket_category_pipeline] no candidates returned from hybrid retrieval")
            main_category, sub_category = _resolve_category("", "", message, valid_categories)
            return main_category, sub_category, 0.0

        # A single pass has nothing to vote against — stay deterministic
        # (temperature=0) and report full confidence rather than spending a
        # sampling-temperature call for no benefit.
        if self_consistency_n <= 1:
            main_category, sub_category = await _classify_hierarchical(message, candidates, valid_categories)
            return main_category, sub_category, 1.0

        sampling_llm = _get_sampling_llm(0.7)
        runs = await asyncio.gather(
            *[
                _classify_hierarchical(message, candidates, valid_categories, llm_client=sampling_llm)
                for _ in range(self_consistency_n)
            ]
        )
        tally = Counter(runs)
        (main_category, sub_category), agree_count = tally.most_common(1)[0]
        confidence = agree_count / self_consistency_n
        return main_category, sub_category, confidence
    except Exception as exc:
        print(
            f"[classify_ticket_category_pipeline] ERROR during classification "
            f"({type(exc).__name__}: {exc}) — falling back to keyword-overlap resolution"
        )
        main_category, sub_category = _resolve_category("", "", message, valid_categories)
        return main_category, sub_category, 0.0


@lru_cache(maxsize=4)
# [HELPER] Cached higher-temperature client for classify_ticket_category_
# pipeline()'s self-consistency sampling.
def _get_sampling_llm(temperature: float):
    """Separate client from the module's shared `llm` (temperature=0, see
    core/llm.py's get_chat_model()) — every other classifier in this file
    wants deterministic output, but self-consistency voting needs actual
    variation across passes to be meaningful. At temperature=0, three
    passes would return the same pick every time and confidence would
    always read 1.0, defeating the point. Mirrors get_chat_model()'s
    provider branching so this still works if LLM_PROVIDER is switched to
    gemini."""
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = settings.LLM_API_KEY or settings.GOOGLE_API_KEY
        return ChatGoogleGenerativeAI(
            model=settings.LLM_MODEL, google_api_key=api_key, temperature=temperature
        )
    api_key = settings.LLM_API_KEY or settings.OPENAI_API_KEY
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=api_key,
        base_url=settings.LLM_BASE_URL,
        temperature=temperature,
    )


# [HELPER] Loads the authoritative (category_name, subcategory, description,
# keywords) list from Postgres for the vector/finetuned/examples classifiers
# and _resolve_category() to validate against.
def _load_valid_categories(log_prefix: str) -> list[tuple[str, str, str, str]]:
    """Shared DB-truth loader for the vector/hybrid classifiers — same
    (category_name, subcategory, description, keywords) tuple shape
    _resolve_category() expects."""
    try:
        all_categories = list_categories()
    except Exception as exc:
        print(f"[{log_prefix}] list_categories() error: {exc}")
        all_categories = []

    return [
        (
            c.get("category_name", ""),
            c.get("subcategory", ""),
            c.get("description", "") or "",
            c.get("keywords", "") or "",
        )
        for c in all_categories
        if c.get("category_name")
    ]


@lru_cache(maxsize=8)
# [HELPER] Cached ChatOpenAI client for a caller-supplied fine-tuned model id,
# used by the two classify_ticket_category_finetuned* eval variants below.
def _get_finetuned_llm(model_id: str):
    """Cached ChatOpenAI client for a fine-tuned model ID. Separate from the
    module's shared `llm` (bound to settings.LLM_MODEL) since model_id is
    caller-supplied and varies per fine-tuning run.

    Explicitly resolves the API key the same way core/llm.py's
    get_chat_model() does (LLM_API_KEY, falling back to OPENAI_API_KEY) —
    without this, ChatOpenAI falls back to whatever OPENAI_API_KEY
    environment variable happens to be set, which can belong to a
    different OpenAI project than the one the fine-tuning job actually
    ran under (domain/helpdesk/scripts/run_finetune_job.py resolves the key the
    same explicit way), causing a 403 model_not_found on every call."""
    api_key = settings.LLM_API_KEY or settings.OPENAI_API_KEY
    return ChatOpenAI(model=model_id, api_key=api_key, temperature=0)


# [EVAL-ONLY] Category classifier using a fine-tuned model with no category
# list/candidates shown — relies entirely on what the model learned during
# fine-tuning. For run_accuracy_eval.py --method finetuned.
async def classify_ticket_category_finetuned(message: str, model_id: str) -> tuple[str, str]:
    """Fine-tuned-model classifier, for accuracy A/B comparison (see
    run_accuracy_eval.py --method finetuned). Standalone from draft_ticket().

    No category list or retrieved candidates are shown — the model was
    fine-tuned on real historical tickets (domain/helpdesk/scripts/
    prepare_finetune_data.py + run_finetune_job.py) and is expected to have
    learned the category mapping, including the statistical base rate for
    ambiguous/generic phrasing, directly from training. The final answer is
    still validated against the authoritative Postgres category list via
    _resolve_category(), exactly like every other classifier here.
    """
    valid_categories = _load_valid_categories("classify_ticket_category_finetuned")

    system_prompt = {
        "role": "system",
        "content": category_finetuned_system_prompt(message),
    }
    response = await _get_finetuned_llm(model_id).ainvoke(
        [system_prompt], config={"tags": [INTERNAL_LLM_TAG]}
    )
    reply = _message_to_text(response)
    main_category, sub_category = _parse_category_draft(reply)

    return _resolve_category(main_category, sub_category, message, valid_categories)


# [EVAL-ONLY] Hybrid of classify_ticket_category_vector() and
# classify_ticket_category_finetuned(): vector-retrieved candidates, but
# decided by the fine-tuned model. For run_accuracy_eval.py --method finetuned_vector.
async def classify_ticket_category_finetuned_vector(
    message: str, model_id: str, k: int = 5
) -> tuple[str, str]:
    """Hybrid of classify_ticket_category_vector() and
    classify_ticket_category_finetuned(): same vector-retrieval top-k
    candidates and same category_vector_system_prompt() framing, but the
    final decision call is made by the fine-tuned model instead of the
    shared base `llm` — combining the model's learned base-rate knowledge
    with per-ticket semantic retrieval. For accuracy A/B comparison (see
    run_accuracy_eval.py --method finetuned_vector).
    """
    candidates = await search_category_candidates(message, k=k)
    valid_categories = _load_valid_categories("classify_ticket_category_finetuned_vector")

    if not candidates:
        print("[classify_ticket_category_finetuned_vector] no candidates returned from vector search")
        return _resolve_category("", "", message, valid_categories)

    candidates_text = _format_category_candidates(candidates)
    system_prompt = {
        "role": "system",
        "content": category_vector_system_prompt(message, candidates_text),
    }

    response = await _get_finetuned_llm(model_id).ainvoke(
        [system_prompt], config={"tags": [INTERNAL_LLM_TAG]}
    )
    reply = _message_to_text(response)
    main_category, sub_category = _parse_category_draft(reply)

    return _resolve_category(main_category, sub_category, message, valid_categories)


# [HELPER] Renders search_ticket_examples() results (real historical
# tickets + their confirmed category) as numbered text blocks for the LLM
# prompt in classify_ticket_category_examples().
def _format_ticket_examples(examples: list[dict]) -> str:
    """Render search_ticket_examples() results as numbered blocks for
    category_examples_system_prompt() — each real ticket's own text plus
    its already-confirmed category, not a hand-written KB description."""
    blocks: list[str] = []
    for i, e in enumerate(examples, start=1):
        blocks.append(
            f'{i}. Past ticket: "{e.get("example_text", "N/A")}"\n'
            f"   Confirmed category: {e.get('main_category', 'N/A')} | "
            f"Sub-category: {e.get('sub_category', 'N/A')}"
        )
    return "\n\n".join(blocks)


# [EVAL-ONLY] Retrieval-augmented classifier using real historical tickets
# instead of the hand-written category KB. For run_accuracy_eval.py --method examples.
async def classify_ticket_category_examples(message: str, k: int = 5) -> tuple[str, str]:
    """Retrieval-augmented classifier using REAL historical tickets (ingested
    by domain/helpdesk/scripts/ingest_ticket_examples.py) instead of the 75-row
    hand-written KB search_category_candidates() uses. Same architecture as
    classify_ticket_category_vector() (embed → search → show LLM candidates
    → LLM decides) — no model training involved — just a larger, more
    representative reference corpus. For accuracy A/B comparison (see
    run_accuracy_eval.py --method examples).
    """
    examples = await search_ticket_examples(message, k=k)
    valid_categories = _load_valid_categories("classify_ticket_category_examples")

    if not examples:
        print("[classify_ticket_category_examples] no examples returned from ticket-example search")
        return _resolve_category("", "", message, valid_categories)

    examples_text = _format_ticket_examples(examples)
    system_prompt = {
        "role": "system",
        "content": category_examples_system_prompt(message, examples_text),
    }

    response = await llm.ainvoke([system_prompt], config={"tags": [INTERNAL_LLM_TAG]})
    reply = _message_to_text(response)
    main_category, sub_category = _parse_category_draft(reply)

    return _resolve_category(main_category, sub_category, message, valid_categories)
