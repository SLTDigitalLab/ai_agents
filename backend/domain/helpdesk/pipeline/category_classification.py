"""
CATEGORY PREDICTION PIPELINE — every classify_ticket_category*() variant
used to turn a user's issue text into a (main_category, sub_category) pair,
plus the shared validation/formatting helpers they all rely on.

classify_ticket_category_pipeline() is the one wired into the live graph
(called from ticket_draft.py): Hybrid Retrieval -> Hierarchical LLM
Classifier -> self-consistency Confidence Check.

Every other classify_ticket_category_*() function here is [EVAL-ONLY] —
kept for run_accuracy_eval.py's A/B comparison and as a rollback path, not
called from the live graph. See helpdesk-category-accuracy-gap project
memory for the accuracy numbers behind picking the pipeline classifier.

All classifiers converge on _resolve_category() to snap the LLM's proposed
category text onto a real row from the Postgres category list — the LLM's
own wording is never trusted as-is.
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

# Below this self-consistency agreement fraction, ask the user one
# clarifying question instead of drafting on a guess.
_CATEGORY_CONFIDENCE_THRESHOLD = 0.67

# Three categories describing the same real-world event (an order stuck
# after submission) from three different backend systems' point of view —
# a customer can't say which system owns it, so a low-confidence
# disagreement between these three skips the clarifying question.
_AMBIGUOUS_CATEGORY_CLUSTER = {
    "SOA",
    "CRM OM - After Submit Issues",
    "Clarity OSS - Order Issues",
}


def _parse_category_draft(reply: str) -> tuple[str, str]:
    """Extract the **Category:** / **Sub-category:** values an LLM reply
    is expected to contain."""
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


def _resolve_category(
    main_category: str,
    sub_category: str,
    original_query: str,
    valid_categories: list[tuple[str, str, str, str]],
) -> tuple[str, str]:
    """Snap an LLM-proposed category/subcategory onto a real DB row —
    exact match, then same-category partial/keyword match, then global
    keyword-overlap fallback. Returns the LLM's own text unchanged if
    valid_categories is empty."""
    if not valid_categories:
        return main_category, sub_category

    matched: tuple[str, str, str, str] | None = None

    # Exact match on BOTH fields first — matching category_name alone
    # would grab whichever subcategory sorts first under that name.
    matched = next(
        (
            (cn, sc, desc, kw)
            for cn, sc, desc, kw in valid_categories
            if cn.lower() == main_category.lower()
            and sc.lower() == sub_category.lower()
        ),
        None,
    )

    # category_name matches exactly but subcategory doesn't — stay within
    # that category's own subcategories.
    if not matched and main_category:
        same_category = [
            (cn, sc, desc, kw)
            for cn, sc, desc, kw in valid_categories
            if cn.lower() == main_category.lower()
        ]
        if same_category:
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
            # No subcategory text match — pick by keyword overlap against
            # the subcategory's name/description/keywords instead of
            # defaulting to whichever row sorts first.
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

    # Partial string match on category_name (e.g. "Networking" vs "Network").
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
    # Last resort: keyword overlap across all categories.
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
# category list via list_categories_tool.
async def classify_ticket_category(message: str) -> tuple[str, str]:
    """Run the same category classification draft_ticket() used to use,
    standalone, for batch accuracy-evaluation scripts."""
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


def _format_category_candidates(candidates: list[dict]) -> str:
    """Render search_category_candidates() results as numbered blocks for
    category_vector_system_prompt()."""
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


# [EVAL-ONLY] Formerly the live classifier before classify_ticket_category_
# pipeline() replaced it. Retrieves top-k similar categories from the
# category-KB Qdrant collection, asks the LLM to pick from just those.
async def classify_ticket_category_vector(message: str, k: int = 5) -> tuple[str, str]:
    """Vector-retrieval variant of classify_ticket_category(), for accuracy
    A/B comparison (run_accuracy_eval.py --method vector)."""
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


def _format_hybrid_candidates(candidates: list[dict]) -> str:
    """Render search_hybrid_candidates() results as numbered blocks,
    tagging each candidate's source (kb / example / kb+example)."""
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


# Two-step classifier: pick the main category from the hybrid-retrieval
# candidates, then pick the sub-category scoped to that main category.
async def _classify_hierarchical(
    message: str,
    candidates: list[dict],
    valid_categories: list[tuple[str, str, str, str]],
    llm_client=None,
) -> tuple[str, str]:
    """llm_client defaults to the shared deterministic `llm`;
    classify_ticket_category_pipeline() passes a higher-temperature client
    when self_consistency_n > 1 so passes can actually disagree.

    Both calls pass tags=[INTERNAL_LLM_TAG] so routers/chat.py's stream
    listener filters these internal "**Category:** X" replies out of the
    user-facing stream — without it, concurrent self-consistency passes
    interleave their tokens into garbage ahead of the real reply."""
    client = llm_client or llm

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


# [LIVE] Full pipeline: Hybrid Retrieval -> Hierarchical LLM Classifier ->
# Confidence Check. Called from ticket_draft.py.
#
# Confidence is self-consistency: run the hierarchical classifier
# self_consistency_n times at temperature > 0 and take the majority vote;
# confidence is that majority's vote share.
async def classify_ticket_category_pipeline(
    message: str, k: int = 5, self_consistency_n: int = 3
) -> tuple[str, str, float]:
    """Returns (main_category, sub_category, confidence) — confidence is
    the fraction of self_consistency_n passes that agreed.

    Never raises: falls back to keyword-overlap resolution (confidence 0.0)
    on any error, so an LLM/embedding failure mid-classification degrades
    to a low-confidence fallback instead of killing the ticket-creation turn."""
    valid_categories = _load_valid_categories("classify_ticket_category_pipeline")

    try:
        candidates = await search_hybrid_candidates(message, k=k)

        if not candidates:
            print("[classify_ticket_category_pipeline] no candidates returned from hybrid retrieval")
            main_category, sub_category = _resolve_category("", "", message, valid_categories)
            return main_category, sub_category, 0.0

        # A single pass has nothing to vote against — stay deterministic
        # and report full confidence.
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
def _get_sampling_llm(temperature: float):
    """Higher-temperature client for self-consistency sampling — at
    temperature=0 every pass would pick the same answer and confidence
    would always read 1.0."""
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


def _load_valid_categories(log_prefix: str) -> list[tuple[str, str, str, str]]:
    """Load the (category_name, subcategory, description, keywords) list
    from Postgres for the classifiers and _resolve_category() to use."""
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
def _get_finetuned_llm(model_id: str):
    """Cached client for a fine-tuned model id. Resolves the API key the
    same way core/llm.py does (LLM_API_KEY, else OPENAI_API_KEY) — without
    that it could pick up an OPENAI_API_KEY from a different project than
    the one the fine-tune actually ran under."""
    api_key = settings.LLM_API_KEY or settings.OPENAI_API_KEY
    return ChatOpenAI(model=model_id, api_key=api_key, temperature=0)


# [EVAL-ONLY] Fine-tuned model, no category list shown — relies on what
# the model learned during fine-tuning.
async def classify_ticket_category_finetuned(message: str, model_id: str) -> tuple[str, str]:
    """Fine-tuned-model classifier, for accuracy A/B comparison
    (run_accuracy_eval.py --method finetuned)."""
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


# [EVAL-ONLY] Vector-retrieved candidates, decided by the fine-tuned model.
async def classify_ticket_category_finetuned_vector(
    message: str, model_id: str, k: int = 5
) -> tuple[str, str]:
    """Hybrid of classify_ticket_category_vector() and
    classify_ticket_category_finetuned() (run_accuracy_eval.py --method
    finetuned_vector)."""
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


def _format_ticket_examples(examples: list[dict]) -> str:
    """Render search_ticket_examples() results as numbered blocks for
    category_examples_system_prompt()."""
    blocks: list[str] = []
    for i, e in enumerate(examples, start=1):
        blocks.append(
            f'{i}. Past ticket: "{e.get("example_text", "N/A")}"\n'
            f"   Confirmed category: {e.get('main_category', 'N/A')} | "
            f"Sub-category: {e.get('sub_category', 'N/A')}"
        )
    return "\n\n".join(blocks)


# [EVAL-ONLY] Retrieval-augmented classifier using real historical tickets
# instead of the hand-written category KB.
async def classify_ticket_category_examples(message: str, k: int = 5) -> tuple[str, str]:
    """Same architecture as classify_ticket_category_vector() but retrieves
    real past tickets instead (run_accuracy_eval.py --method examples)."""
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
