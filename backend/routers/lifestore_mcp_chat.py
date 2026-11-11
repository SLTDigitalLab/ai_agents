from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import traceback
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from services.lifestore_availability import check_live_availability, unverified
from services.lifestore_identity import CLARIFICATION, explicit_identity_query
from services.lifestore_prices import (
    PRICE_INTENTS, parse_price, product_price,
    historical_price_reference,
    apply_price_constraints as _apply_price_constraints,
)
from services.lifestore_memory import (
    get_conversation_memory,
    save_message,
    save_products_shown,
    set_current_category,
    set_last_selected_product,
    set_pending_action,
    get_summarization_payload,
    apply_conversation_summary,
)
from services.lifestore_query_intent import (
    broad_category_query, category_price_bound, category_price_intent,
    remembered_ordinal_query, implicit_price_plan,
)

router = APIRouter(prefix="/api/v1/lifestore", tags=["LifeStore MCP"])


class LifeStoreMCPChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    agent_id: str = "ask_lifestore"
    user_id: str = "anonymous"
    thread_id: str | None = None
    use_mcp: bool = True
    mcp_server: str | None = "mcp_lifestore"
    tool_name: str | None = "lifestore_hybrid_product_search"
    preferred_tool: str | None = "lifestore_hybrid_product_search"
    force_tool_call: bool = True
    limit: int = 5


_MCP_MODULE: Any = None





def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min(parsed, maximum), minimum)

_LOW_SIGNAL_QUERY = re.compile(
    r"^(?:[\d,.\s]+|rs\.?|lkr|one|it|that|this|them|these|those|"
    r"cheap(?:est)?|expensive|most|least|lowest|highest|price|cost)"
    r"(?:\s+(?:[\d,.\s]+|one|it|that|this))*$",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _get_lifestore_openai_llm():
    api_key = os.getenv("LIFESTORE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

    if not api_key:
        try:
            from core.config import settings

            api_key = settings.OPENAI_API_KEY
        except Exception:
            api_key = None

    if not api_key:
        return None

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("LIFESTORE_OPENAI_MODEL", "gpt-4.1-mini"),
        api_key=api_key,
        temperature=0,
    )

class LifestorePlan(BaseModel):
    """Schema the planner LLM must return. Enforced via structured output —
    the model cannot emit an invalid answer_mode/price_intent or skip a
    field, so this replaces free-text JSON parsing for the planning call."""

    answer_mode: Literal[
        "greeting", "single_product", "availability", "category_browse",
        "comparison", "purchase", "general",
    ] = Field(..., description="The single best-matching intent for the latest message.")
    product_query: str = Field(default="", description=(
        "Clean retrieval phrase: product name, product ID, category, or "
        "search phrase. Empty string if nothing should be searched yet "
        "(e.g. a bare price constraint that Python will fill in from memory)."
    ))
    comparison_queries: list[str] = Field(default_factory=list, description=(
        "Exact product names for comparison/availability-of-several-named-products. Max 6."
    ))
    desired_product_count: int = Field(default=5, ge=0, le=8)
    show_product_cards: bool = Field(default=True)
    open_lifestore_form: bool = Field(default=False, description=(
        "True only when the user clearly wants to buy/order/purchase/place an order."
    ))
    needs_comparison_table: bool = Field(default=False)
    price_intent: Literal[
        "none", "cheapest", "most_expensive", "under", "over", "between",
        "cheaper_than", "more_expensive_than",
    ] = Field(default="none")
    min_price: float | None = Field(default=None)
    max_price: float | None = Field(default=None)
    reference_price: float | None = Field(default=None, description=(
        "Always null. Python resolves this from stored facts — never compute it yourself."
    ))


@lru_cache(maxsize=1)
def _get_lifestore_planner_llm():
    """Schema-bound LLM for the planning step only (pure classification,
    no user-facing prose — optionally a different/stronger model than the
    conversational answer-writer without affecting its cost/latency)."""
    api_key = os.getenv("LIFESTORE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        try:
            from core.config import settings
            api_key = settings.OPENAI_API_KEY
        except Exception:
            api_key = None
    if not api_key:
        return None
    from langchain_openai import ChatOpenAI
    base = ChatOpenAI(
        model=os.getenv("LIFESTORE_PLANNER_MODEL", "gpt-4.1-mini"),
        api_key=api_key,
        temperature=0,
    )
    return base.with_structured_output(LifestorePlan)    
def _maybe_summarize_conversation(
    thread_id: str | None,
) -> None:
    """
    Compress older chat messages into a running summary.

    The newest messages remain exact.

    Structured state such as:
    - last_products_shown
    - last_selected_product
    - current_category
    - pending_action

    is handled separately and is never summarized here.
    """

    if not thread_id:
        return

    payload = get_summarization_payload(
        thread_id=thread_id,
        trigger_count=14,
        keep_recent=6,
    )

    # Conversation is still short.
    if payload is None:
        return

    llm = _get_lifestore_openai_llm()

    # If OpenAI is unavailable, simply keep the messages.
    # Do NOT delete anything.
    if llm is None:
        return

    existing_summary = payload.get(
        "existing_summary",
        "",
    )

    messages_to_summarize = payload.get(
        "messages_to_summarize",
        [],
    )

    recent_messages = payload.get(
        "recent_messages",
        [],
    )

    old_messages_text = "\n".join(
        f"{item.get('role', 'unknown')}: "
        f"{item.get('content', '')}"
        for item in messages_to_summarize
    )

    try:
        response = llm.invoke(
            [
                (
                    "system",
                    (
                        "You maintain compact conversation memory "
                        "for a LifeStore shopping assistant. "

                        "Create a concise factual running summary "
                        "of the older conversation. "

                        "Preserve information that may matter in "
                        "future turns, including: "
                        "the user's shopping goal, "
                        "product categories discussed, "
                        "important product names or product IDs, "
                        "products the user showed interest in, "
                        "comparisons or decisions already made, "
                        "purchase intentions, "
                        "and unresolved requests. "

                        "Do not invent information. "

                        "Do not treat old stock status or old prices "
                        "as permanently authoritative because those "
                        "may change and should be retrieved again. "

                        "Do not include unnecessary greetings, "
                        "small talk, formatting instructions, "
                        "or repeated assistant wording. "

                        "If an existing summary is supplied, merge "
                        "the new older messages into it rather than "
                        "discarding useful existing information. "

                        "Return only the updated summary."
                    ),
                ),
                (
                    "human",
                    (
                        "Existing running summary:\n"
                        f"{existing_summary or 'None yet.'}\n\n"

                        "Older messages to incorporate:\n"
                        f"{old_messages_text}"
                    ),
                ),
            ]
        )

        new_summary = _safe_text(
            getattr(
                response,
                "content",
                response,
            )
        )

        if not new_summary:
            return

        apply_conversation_summary(
            thread_id=thread_id,
            summary=new_summary,
            recent_messages=recent_messages,
        )

    except Exception as error:
        # Summarization should NEVER break the user's chat.
        print(
            "LifeStore conversation summarization failed:",
            str(error),
        )


def _extract_json_object(text: Any) -> dict[str, Any]:
    value = _safe_text(text)
    if not value:
        return {}

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def _fallback_plan(message: str, requested_limit: int) -> dict[str, Any]:
    """
    Used only when OpenAI is unavailable.

    Normal behavior is fully LLM-planned through _plan_lifestore_answer.
    This fallback is deliberately conservative so single-product queries do not
    accidentally return 5+ cards.
    """
    count = _bounded_int(requested_limit, 1, 1, 8)
    implicit = implicit_price_plan(message)
    if implicit:
        return _normalize_plan({
            "answer_mode": "category_browse",
            "product_query": "",
            "desired_product_count": implicit.get("desired_product_count", 5),
            **implicit,
        }, message, requested_limit)
    category_query = broad_category_query(message)
    ranked_category = category_price_intent(message)
    bounded_category = category_price_bound(message)
    if category_query or ranked_category or bounded_category:
        product_query = (ranked_category[0] if ranked_category else
                         bounded_category[0] if bounded_category else category_query)
        price_intent = ranked_category[1] if ranked_category else "none"
        min_price = None
        max_price = None
        if bounded_category:
            product_query, price_intent, amount = bounded_category
            if price_intent == "under":
                max_price = amount
            else:
                min_price = amount
        return {
            "answer_mode": "category_browse",
            "product_query": product_query,
            "comparison_queries": [],
            "desired_product_count": 1 if ranked_category else count,
            "show_product_cards": True,
            "open_lifestore_form": False,
            "needs_comparison_table": False,
            "price_intent": price_intent,
            "min_price": min_price,
            "max_price": max_price,
            "reference_price": None,
        }
    return {
        "answer_mode": "availability" if re.search(
            r"\b(available|availability|in stock|out of stock)\b|\bdo you have\b|\bcan i buy\b.*\bnow\b",
            message, re.I) else "single_product",
        "product_query": message,
        "comparison_queries": [],
        "desired_product_count": min(count, 1),
        "show_product_cards": True,
        "open_lifestore_form": False,
        "needs_comparison_table": False,
        "price_intent": "none",
        "min_price": None,
        "max_price": None,
        "reference_price": None,
    }

def _normalize_plan(parsed: dict[str, Any], message: str, requested_limit: int) -> dict[str, Any]:
    allowed_modes = {
        "greeting",
        "single_product",
        "availability",
        "category_browse",
        "comparison",
        "purchase",
        "general",
    }

    mode = _safe_text(parsed.get("answer_mode")).lower()
    if mode not in allowed_modes:
        mode = "single_product"

    category_query = broad_category_query(message)
    ranked_category = category_price_intent(message)
    bounded_category = category_price_bound(message)
    if mode == "single_product" and (category_query or ranked_category or bounded_category):
        mode = "category_browse"

    comparison_queries = parsed.get("comparison_queries") or []
    if not isinstance(comparison_queries, list):
        comparison_queries = []

    comparison_queries = [
        _safe_text(item)
        for item in comparison_queries
        if _safe_text(item)
    ][:6]

    product_query = _safe_text(parsed.get("product_query"))
    if mode == "category_browse" and (category_query or ranked_category or bounded_category):
        product_query = (ranked_category[0] if ranked_category else
                         bounded_category[0] if bounded_category else category_query)
    elif not product_query:
        product_query = message

    desired_default = {
        "single_product": 1,
        "availability": 1,
        "purchase": 1,
        "comparison": 4,
        "category_browse": 5,
        "general": min(max(int(requested_limit or 5), 1), 5),
    }.get(mode, 1)

    desired_count = _bounded_int(
        parsed.get("desired_product_count"),
        desired_default,
        1,
        8,
    )

    if mode == "greeting":
        desired_count = 0
    elif mode in {"single_product", "availability", "purchase"}:
        desired_count = 1
    elif mode == "comparison":
        desired_count = max(min(desired_count, 6), 2)

    # Greetings never show cards; for comparison the answer is a table, not a pile of cards.
    if mode == "greeting":
        show_product_cards = False
    else:
        show_product_cards = _as_bool(parsed.get("show_product_cards"), mode != "comparison")

    price_intent = _safe_text(parsed.get("price_intent")).lower()
    if price_intent not in PRICE_INTENTS:
        price_intent = "none"
    if ranked_category:
        price_intent = ranked_category[1]
        desired_count = 1
    if bounded_category:
        _, price_intent, bound = bounded_category
    if price_intent != "none" and not _safe_text(parsed.get("product_query")):
        product_query = ""

    # a list request must never collapse to 1 product
    LIST_PRICE_INTENTS = {"under", "over", "between", "cheaper_than", "more_expensive_than"}
    if mode == "category_browse" and price_intent in LIST_PRICE_INTENTS:
        desired_count = max(desired_count, 5)
    if mode == "category_browse":
        show_product_cards = True

    min_price = parse_price(parsed.get("min_price"))
    max_price = parse_price(parsed.get("max_price"))
    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price
    if bounded_category:
        if price_intent == "under":
            min_price, max_price = None, bound
        else:
            min_price, max_price = bound, None

    return {
        "price_intent": price_intent,
        "min_price": min_price,
        "max_price": max_price,
        "reference_price": parse_price(parsed.get("reference_price")),
        "answer_mode": mode,
        "product_query": product_query,
        "comparison_queries": comparison_queries,
        "desired_product_count": desired_count,
        "show_product_cards": show_product_cards,
        "open_lifestore_form": _as_bool(parsed.get("open_lifestore_form"), False),
        "needs_comparison_table": _as_bool(parsed.get("needs_comparison_table"), mode == "comparison"),
    }


def _plan_lifestore_answer(
    message: str,
    requested_limit: int,
    conversation_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:

    memory = conversation_memory or {}
    ordinal_query = remembered_ordinal_query(message, memory)
    if ordinal_query:
        return _normalize_plan({
            "answer_mode": "single_product",
            "product_query": ordinal_query,
            "desired_product_count": 1,
        }, message, requested_limit)

 

    
    planner = _get_lifestore_planner_llm()

    if planner is None:
        return _fallback_plan(message, requested_limit)

    history = memory.get("messages") or []
    summary = str(memory.get("summary") or "")
    state = memory.get("state") or {}

    current_category = state.get("current_category")
    last_products = state.get("last_products_shown") or []
    last_selected_product = state.get("last_selected_product")
    pending_action = state.get("pending_action")

    history_text = "\n".join(
        f"{item['role']}: {item['content']}"
        for item in history[-6:]
    )

    products_text = "\n".join(
        (
            f"{index}. "
            f"{product.get('name') or 'Unknown product'} "
            f"(ID: {product.get('product_id') or 'unknown'}, "
            f"Price: {product_price(product)}, "
            f"Type: {product.get('product_type') or 'unknown'}, "
            f"Category: {product.get('category') or 'unknown'})"
        )
        for index, product in enumerate(
            last_products,
            start=1,
        )
    )

    selected_product_text = (
        json.dumps(
            last_selected_product,
            ensure_ascii=False,
        )
        if last_selected_product
        else "None"

    )

    LIFESTORE_PLANNER_SYSTEM_PROMPT = """
You are the intent and retrieval planner for Ask LifeStore.

You do not answer the user and you do not retrieve products yourself. You
produce only the structured plan defined by the schema — never invent
product names, prices, stock status, product IDs, or catalogue information
that is not present in the supplied conversation memory or retrieval
context. Read the user's intent semantically; do not use keyword-only
pattern matching.

## 1. Core rules

1. Understand the user's intent before selecting an answer_mode.
2. Distinguish between: browsing a category, searching for a named
   product, filtering by price, asking for the cheapest/most expensive
   match, referring to a product mentioned or shown earlier, and asking a
   follow-up about a previously displayed product.
3. Do not classify every message containing a product-related word as a
   single_product lookup.
4. Do not treat words such as "cheapest", "budget", "under", "second one",
   or "the deal with" as part of a literal product name — they express an
   intent or a reference, never a name.
5. Preserve the user's intended category and constraints in the plan.
6. Use only the supplied conversation history, current context, and
   memory. Never invent missing context.

## 2. answer_mode classification

Classify the latest message into exactly one of: greeting, single_product,
availability, category_browse, comparison, purchase, general.

**greeting** — a greeting, thanks, farewell, small talk, or a question
about who you are or what you can do. Never recommend a product for
greeting: desired_product_count=0, show_product_cards=false.

**category_browse** — the user wants products from a category without
identifying one exact product, OR wants a price-filtered/ranked
(cheapest, most expensive, under/over/between) set from a category.
Examples: "show me routers", "suggest a budget router", "routers under
5000", "show me the cheapest one", "find the cheapest phone under 10000".
  - product_query is the normalized category/search phrase, never the
    full sentence and never a price word or amount.
  - For a pure superlative/bounded request ("the cheapest router",
    "the most expensive speaker"), desired_product_count is 1.
  - Never choose or calculate the cheapest/most-expensive product
    yourself — Python applies all numeric constraints against real
    retrieved prices.
  - If the category cannot be determined from the message or from
    current_category / last_products_shown, leave product_query empty
    rather than inventing a category; do not silently guess.

**single_product** — the user identifies one particular product by name,
ID, or a resolvable reference (ordinal, "it", "that one", a clear
follow-up about an already-identified product). Remove filler wording
such as "is", "available", "do you have", "tell me about" from
product_query. Never route a category search, a price-filtered search, a
superlative search, or an unresolved positional reference here — those
are category_browse.

**availability** — ONLY explicit current stock/availability questions,
including "do you have this product" and "can I buy this now?". Ordinary
browsing, features, price, and recommendations are NOT availability. For
availability of several specific named products, put ONLY their exact
names in comparison_queries (max 6). For "are these available", use the
names from last_products_shown. Never expand this to the whole category.

**comparison** — the user is comparing named products or asks to compare.
Include comparison_queries only when the user clearly names specific
products. desired_product_count is 2-6. needs_comparison_table=true.
show_product_cards=false unless the user explicitly asks to see product
cards/images (a comparison answer is a table, not a pile of cards).

**purchase** — open_lifestore_form=true ONLY when the user clearly wants
to buy/order/purchase/place an order. desired_product_count=1.

**general** — anything else that doesn't fit the above.

## 3. Resolving references to previously displayed products

- The structured memory lists products previously shown, in order.
  Treat that numeric order as authoritative for "first one", "second
  one", "third one", "last one", and similar.
- If the user refers to a specific previous product, resolve it to that
  product's EXACT name and use that exact name as product_query — never
  the literal phrase "second one". Example: last_products_shown contains
  "1. Router A, 2. Router B" and the user says "tell me about the second
  one" -> product_query="Router B", answer_mode=single_product.
- Use last_selected_product when the user says "it", "that product", or
  "that one" and clearly means the currently discussed product.
- Use current_category to understand "other ones", "more like these",
  "cheaper ones", or a bare superlative/price request with no category
  named in the current message.
- Prefer the most recent relevant product list; do not resolve a
  reference against an older, unrelated list when a newer one exists.
- If the reference is ambiguous or the needed memory is missing, leave
  product_query empty rather than guessing — do not silently pick a
  product.

## 4. Price interpretation

Recognize: "under/below/less than", "over/above/more than",
"between X and Y", "cheapest/lowest price", "most expensive/highest
price", and "budget"/"affordable" when a constraint can be established
from context (even with no number attached).

Extract price_intent: none, cheapest, most_expensive, under, over,
between, cheaper_than, or more_expensive_than.
  - Set max_price for under, min_price for over, both for between;
    unused bounds stay null.
  - Use category_browse for any price-based request and strip price
    words/amounts out of product_query.
  - For "cheaper ones" / "anything cheaper", resolve the PRODUCT TYPE
    from memory (not an exact model name) and use cheaper_than. For
    "more expensive ones", use more_expensive_than.
  - Leave reference_price null for contextual comparisons ("cheaper than
    this", "cheaper ones") — the backend derives it from last_selected_
    product or last_products_shown. If the user states a numeric
    comparison price explicitly ("under 8000"), use under/over with that
    bound instead of cheaper_than/more_expensive_than.
  - Do not invent a numeric budget when the user only says "budget" or
    "affordable" with no number — that is still price_intent=cheapest or
    a bare category request, never a fabricated max_price.
  - Never confuse a price constraint with a product identity.
    - desired_product_count: use 1 ONLY for a singular superlative ("the cheapest
    router", "which is the most expensive one"). For under/over/between/
    cheaper_than/more_expensive_than, or any plural request ("routers cheaper
    than this", "show me routers over 5000"), use 5. Always set
    show_product_cards=true for category_browse.

## 5. Worked examples

1. current_category: "router". Message: "tell me about the cheapest one"
   -> answer_mode=category_browse, product_query="router" (inherited
   from current_category, not invented), price_intent=cheapest,
   desired_product_count=1.
2. current_category: "router". Message: "gimme the budget one" -> same as
   above — "budget" is a synonym for cheapest, not a literal keyword.
3. last_products_shown: "1. TP-Link Archer C6, 2. Netgear R6700".
   Message: "whats the deal with the second one here" ->
   answer_mode=single_product, product_query="Netgear R6700" (the exact
   name, resolved by position).
4. current_category: "router". Message: "anything below 8k" ->
   answer_mode=category_browse, product_query="router" (inherited),
   price_intent=under, max_price=8000.
5. last_selected_product: "Netgear R6700". Message: "is there anything
   cheaper than this" -> product_query="" (Python resolves the reference
   price and category from last_selected_product), price_intent=
   cheaper_than, reference_price=null.
6. No current_category, no last_products_shown. Message: "show me the
   cheapest one" -> product_query="" (nothing to inherit; do not guess a
   category), price_intent=cheapest.
7. Message: "routers between 5000 and 15000" -> answer_mode=
   category_browse, product_query="router", price_intent=between,
   min_price=5000, max_price=15000.
8. last_products_shown has 2 routers. Message: "are these available" ->
   answer_mode=availability, comparison_queries=[exact names of both].

## 6. Final check before returning the plan

1. Is answer_mode consistent with what the user actually asked, not just
   a keyword in the message?
2. Is a category/price/superlative request being wrongly classified as
   single_product?
3. Have price words and numeric bounds been fully separated out of
   product_query?
4. Has every previous-product reference been resolved to an exact name
   (or left empty if it can't be resolved), never left as the literal
   reference phrase?
5. Are price_intent and min_price/max_price/reference_price internally
   consistent (right bounds set, unused ones null)?
6. Does the plan match the schema's allowed answer_mode and price_intent
   values exactly?
7. Is anything required actually missing or ambiguous? If so, prefer an
   empty product_query over an invented one.

Correct the plan against these checks before returning it.
"""
    

    try:
        plan_obj = planner.invoke(
            [
                ("system", LIFESTORE_PLANNER_SYSTEM_PROMPT),
                (
                    "human",
                    (
                        "Conversation memory:\n\n"

                        f"Summary:\n"
                        f"{summary or 'No summary yet.'}\n\n"

                        f"Recent messages:\n"
                        f"{history_text or 'No previous messages.'}\n\n"

                        f"Current category:\n"
                        f"{current_category or 'None'}\n\n"

                        f"Last products shown:\n"
                        f"{products_text or 'None'}\n\n"

                        f"Last selected product:\n"
                        f"{selected_product_text}\n\n"

                        f"Pending action:\n"
                        f"{pending_action or 'None'}\n\n"

                        "-----------------------------\n"

                        f"Latest user message:\n"
                        f"{message}\n\n"

                        f"Frontend requested limit: "
                        f"{requested_limit}"
                    ),
                ),
            ]
        )

        # Structured output guarantees plan_obj is a valid LifestorePlan —
        # no free-text JSON parsing, no malformed/missing-field failures.
        parsed = plan_obj.model_dump()

        return _normalize_plan(
            parsed,
            message,
            requested_limit,
        )

    except Exception:
        print("PLANNER FAILED:")
        print(traceback.format_exc())
        return _fallback_plan(
            message,
            requested_limit,
        )
        

    



def _resolve_availability_context(plan, memory, message):
    """Keep ordinal/plural references tied to the actual displayed products."""
    if plan.get("answer_mode") != "availability":
        return
    state = memory.get("state") or {}
    shown = state.get("last_products_shown") or []
    ordinals = re.findall(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth)\b", message, re.I)
    if ordinals:
        order = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"]
        indices = list(dict.fromkeys(order.index(word.lower()) for word in ordinals))
        plan["availability_products"] = [shown[i] for i in indices if i < len(shown)]
    elif re.search(r"\b(these|those|all of them)\b", message, re.I):
        plan["availability_products"] = shown[:8]
    elif re.search(r"\b(this|that)\s+(product|router|one)\b|\bis it\b|\bbuy (this|it)\b", message, re.I):
        selected = state.get("last_selected_product")
        plan["availability_products"] = [selected] if selected else shown[:1] if len(shown) == 1 else []
    else:
        queries = plan.get("comparison_queries") or [plan.get("product_query")]
        matches = [p for q in queries for p in shown if q in (p.get("name"), p.get("product_id"))]
        if len(matches) == len(queries):
            plan["availability_products"] = matches


def _resolve_price_context(plan: dict[str, Any], memory: dict[str, Any]) -> None:
    """Resolve relative bounds from actual stored products, never planner arithmetic."""
    intent = plan.get("price_intent", "none")
    if intent == "none":
        return
    state = memory.get("state") or {}

    if intent not in {"cheaper_than", "more_expensive_than"} and not plan.get("product_query"):
        shown = state.get("last_products_shown") or []
        context = state.get("current_category")
        if not context and shown:
            types = {p.get("product_type") or p.get("category") for p in shown}
            if len(types) == 1:
                context = shown[0].get("product_type") or shown[0].get("category")
        if context:
            plan["product_query"] = context
        else:
            plan["price_clarification"] = "What type of product are you looking for?"

    if intent in {"cheaper_than", "more_expensive_than"}:
        selected = state.get("last_selected_product")
        shown = state.get("last_products_shown") or []
        plan["reference_price"] = None
        if selected:
            plan["reference_price"] = product_price(selected)
            plan["reference_price_source"] = selected.get("name") or "selected product"
            context = selected.get("product_type") or state.get("current_category") or selected.get("category")
        else:
            types = {p.get("product_type") or p.get("category") for p in shown}
            prices = [product_price(p) for p in shown]
            prices = [p for p in prices if p is not None]
            context = state.get("current_category")
            if len(types) == 1 and shown:
                context = shown[0].get("product_type") or context or shown[0].get("category")
                if prices:
                    cheaper = intent == "cheaper_than"
                    plan["reference_price"] = min(prices) if cheaper else max(prices)
                    plan["reference_price_source"] = "lowest shown price" if cheaper else "highest shown price"
        if context:
            plan["product_query"] = context
        if not context or plan["reference_price"] is None:
            plan["price_clarification"] = "Which product or price should I compare against, and what type of product are you looking for?"

    required = {
        "under": ("max_price",), "over": ("min_price",),
        "between": ("min_price", "max_price"),
    }.get(intent, ())
    if any(plan.get(field) is None for field in required):
        plan["price_clarification"] = "What price or price range in rupees should I use?"
    if not plan.get("product_query") and not plan.get("price_clarification"):
        plan["price_clarification"] = "What type of product are you looking for?"
def _price_result_intro(plan: dict[str, Any]) -> str:
    intent = plan.get("price_intent", "none")
    if intent == "cheapest":
        return "Lowest-priced matches in the current LifeStore results (lowest price first)."
    if intent == "most_expensive":
        return "Highest-priced matches in the current LifeStore results (highest price first)."
    if intent in {"cheaper_than", "more_expensive_than"}:
        direction = "below" if intent == "cheaper_than" else "above"
        return f"Matches {direction} Rs. {plan['reference_price']:,.2f} ({plan.get('reference_price_source', 'reference price')}) in the current LifeStore results."
    return "Matches for your price constraint in the current LifeStore results."


def _compact_product_for_llm(product: dict[str, Any]) -> dict[str, Any]:
    allowed = [
        "product_id",
        "name",
        "seller",
        "brand",
        "category",
        "product_type",
        "price",
        "price_value",
        "currency",
        "stock_status",
        "availability",
        "stock",
        "url",
        "description",
        "key_details",
        "specs",
        "vector_evidence",
    ]

    compact = {
        key: product.get(key)
        for key in allowed
        if product.get(key) not in (None, "", [], {})
    }

    if isinstance(compact.get("key_details"), list):
        compact["key_details"] = compact["key_details"][:8]

    if isinstance(compact.get("vector_evidence"), list):
        compact["vector_evidence"] = compact["vector_evidence"][:3]

    if isinstance(compact.get("specs"), dict):
        # Keep prompt size under control.
        compact["specs"] = dict(list(compact["specs"].items())[:12])

    return compact


def _format_stock_status(product: dict[str, Any]) -> str:
    value = _safe_text(product.get("stock_status") or product.get("availability"))
    return value.replace("_", " ") if value else "Unknown"


def _fallback_answer(message: str, plan: dict[str, Any], products: list[dict[str, Any]]) -> str:
    mode = _safe_text(plan.get("answer_mode"))
    if not products:
        return "I could not find enough LifeStore product data to answer that from the current knowledge base."

    if mode == "comparison":
        rows = [
            "| Product | Price | Stock status | Category | Product type |",
            "|---|---:|---|---|---|",
        ]
        for product in products:
            rows.append(
                "| "
                + " | ".join(
                    [
                        _safe_text(product.get("name")) or "Unknown",
                        _safe_text(product.get("price")) or "Unknown",
                        _format_stock_status(product),
                        _safe_text(product.get("category")) or "Unknown",
                        _safe_text(product.get("product_type")) or "Unknown",
                    ]
                )
                + " |"
            )
        return "\n".join(rows)

    product = products[0]
    name = _safe_text(product.get("name")) or "This LifeStore product"
    product_type = _safe_text(product.get("product_type"))
    brand = _safe_text(product.get("brand"))
    seller = _safe_text(product.get("seller")) or "the listed seller"

    intro = f"The {name} is"
    if product_type:
        intro += f" a {product_type}"
    if brand:
        intro += f" from {brand}"
    intro += f" sold by {seller}."

    lines = [
        intro,
        "",
        f"Product: {name}",
        f"Brand: {brand or 'Unknown'}",
        f"Seller: {seller}",
        f"Price: {_safe_text(product.get('price')) or 'Unknown'}",
        f"Stock status: {_format_stock_status(product)}",
        f"Category: {_safe_text(product.get('category')) or 'Unknown'}",
        f"Product type: {product_type or 'Unknown'}",
        "",
        "Key details",
    ]

    details = product.get("key_details") or []
    if isinstance(details, list) and details:
        lines.extend(f"- {_safe_text(detail)}" for detail in details[:6])
    else:
        lines.append("- No additional feature details are available in the current LifeStore KB.")

    return "\n".join(lines)



def _markdown_cell(value: Any) -> str:
    text = _safe_text(value) or "-"
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _best_for_product(product: dict[str, Any]) -> str:
    details = product.get("key_details") or []

    if isinstance(details, list):
        for detail in details:
            text = _safe_text(detail)
            if text:
                return text[:110]

    description = _safe_text(product.get("description"))
    if description:
        return description[:110]

    product_type = _safe_text(product.get("product_type"))
    category = _safe_text(product.get("category"))

    if product_type and category:
        return f"{product_type} use under {category}"

    return category or product_type or "General LifeStore product"


def _write_category_markdown_answer(
    message: str,
    plan: dict[str, Any],
    products: list[dict[str, Any]],
) -> str:
    query = _safe_text(plan.get("product_query")) or message
    count = len(products)

    lines = [
        f"Here are the **{query}** products currently found in LifeStore.",
        "",
        "**Overview**",
        f"- Category/search: **{query}**",
        f"- Products found: **{count}**",
        "",
        "**Products**",
        "",
        "| Product | Seller | Price | Stock status | Best for |",
        "|---|---|---:|---|---|",
    ]

    for product in products[:8]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(product.get("name")),
                    _markdown_cell(product.get("seller")),
                    _markdown_cell(product.get("price")),
                    _markdown_cell(_format_stock_status(product)),
                    _markdown_cell(_best_for_product(product)),
                ]
            )
            + " |"
        )

    in_stock = [
        product for product in products
        if "in_stock" in _format_stock_status(product).lower()
        or "in stock" in _format_stock_status(product).lower()
    ]

    lines.extend(
        [
            "",
            "**Important notes**",
            f"- **{len(in_stock)}** of these products are currently marked as in stock.",
            "- Use the product slideshow below to review the matched items with product details.",
        ]
    )

    return "\n".join(lines)

def _write_lifestore_answer(
    message: str,
    plan: dict[str, Any],
    products: list[dict[str, Any]],
    fallback: str,
) -> str:
    if plan.get("answer_mode") == "availability":
        if not products:
            return CLARIFICATION
        return "\n".join(
            f"{p.get('name') or 'This product'}: " + (
                f"currently {_format_stock_status(p)} on the LifeStore website."
                if p.get("availability_verified") is True else
                "I couldn't verify the current availability of this product right now."
            ) for p in products
        )
    if not products and fallback == CLARIFICATION:
        return CLARIFICATION
    if plan.get("price_clarification"):
        return plan["price_clarification"]
    if plan.get("price_intent", "none") != "none" and not products:
        return "I found no products with known prices matching that constraint in the current LifeStore results. Try a different price range."
    llm = _get_lifestore_openai_llm()
    deterministic_fallback = _fallback_answer(message, plan, products) if products else fallback
    if plan.get("price_intent", "none") != "none" and products:
        details = (_write_category_markdown_answer(message, plan, products)
                   if len(products) > 1 else deterministic_fallback)
        deterministic_fallback = _price_result_intro(plan) + "\n\n" + details

    if llm is None:
        return deterministic_fallback

    compact_products = [_compact_product_for_llm(product) for product in products]
    mode = _safe_text(plan.get("answer_mode")) or "single_product"

    # Product-list/category answers should be deterministic and table-first.
    # This avoids raw paragraph formatting from the LLM and keeps the UI clean.
    if mode in {"category_browse", "general"} and len(products) > 1:
        if plan.get("price_intent", "none") != "none":
            return deterministic_fallback
        return _write_category_markdown_answer(message, plan, products)

    try:
        response = llm.invoke(
            [
                (
                    "system",
                    (
                        "You are Ask LifeStore, a shopping assistant for LifeStore products. "
                        "Write the final chat answer using ONLY the supplied product facts. "
                        "Do not invent prices, stock, sellers, categories, links, features, pros, or cons. "
                        "For price requests, products are already filtered and ordered by Python. "
                        "Keep that order and explain only those results. Say 'in the current "
                        "LifeStore results', never claim a store-wide cheapest or most expensive "
                        "product. For relative prices mention the supplied reference_price and "
                        "reference_price_source, including when it is the lowest/highest shown price. "
                        "Do not mention raw image URLs. "
                        "Do not include a Product link line unless the user specifically asks for a link. "
                        "\n\n"
                        "Formatting rules for single_product, availability, and purchase:\n"
                        "1. Start with one natural sentence, e.g. 'The X is an ADSL router sold by SLT-MOBITEL.'\n"
                        "2. Add a blank line.\n"
                        "3. Put each field on its own separate line exactly like:\n"
                        "Product: ...\nBrand: ...\nSeller: ...\nPrice: ...\nStock status: ...\nCategory: ...\nProduct type: ...\n"
                        "4. Add a blank line.\n"
                        "5. Add 'Key details' heading, then concise bullets.\n"
                        "Never cram field labels into one paragraph.\n"
                        "\n\n"
                        "Formatting rules for comparison:\n"
                        "Put a Markdown table FIRST. Columns: Product, Best for, Price, Stock status, Pros, Cons. "
                        "After the table, add one short recommendation sentence. "
                        "Do not render product cards in the text.\n"
                        "\n\n"
                        "For category/general questions, summarize the returned products clearly and avoid forcing everything into one product answer."
                    ),
                ),
                (
                    "human",
                    (
                        f"User question: {message}\n"
                        f"Answer mode: {mode}\n"
                        f"Planner JSON:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
                        "Product facts JSON:\n"
                        f"{json.dumps(compact_products, ensure_ascii=False, indent=2)}"
                    ),
                ),
            ]
        )

        answer = _safe_text(getattr(response, "content", response))
        return answer or deterministic_fallback

    except Exception:
        return deterministic_fallback


def _project_root() -> Path:
    """
    Expected layout:

    ai_agents_mcp_experiment/
      backend/
        routers/
          lifestore_mcp_chat.py
      mcp_lifestore/
        server.py
    """
    return Path(__file__).resolve().parents[2]


def _mcp_server_path() -> Path:
    env_path = os.getenv("LIFESTORE_MCP_SERVER_PATH", "").strip()

    if env_path:
        return Path(env_path).expanduser().resolve()

    return _project_root() / "mcp_lifestore" / "server.py"


def _load_mcp_module() -> Any:
    global _MCP_MODULE

    if _MCP_MODULE is not None:
        return _MCP_MODULE

    root = _project_root()
    server_path = _mcp_server_path()

    if not server_path.exists():
        raise FileNotFoundError(
            f"MCP server.py not found at: {server_path}. "
            "Set LIFESTORE_MCP_SERVER_PATH in .env if your path is different."
        )

    for path in [root, root / "backend"]:
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)

    spec = importlib.util.spec_from_file_location(
        "_ask_lifestore_mcp_server",
        server_path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load MCP server module from: {server_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "lifestore_hybrid_product_search"):
        raise AttributeError(
            "mcp_lifestore/server.py does not define lifestore_hybrid_product_search"
        )

    _MCP_MODULE = module
    return _MCP_MODULE


def _call_mcp_tool(module: Any, tool_name: str, **kwargs: Any) -> dict[str, Any]:
    tool = getattr(module, tool_name, None)
    if not callable(tool):
        raise AttributeError(f"mcp_lifestore/server.py does not define {tool_name}")

    result = tool(**kwargs)

    if isinstance(result, dict):
        return result

    return {
        "status": "success",
        "answer": str(result),
        "products": [],
        "retrieval": {},
    }


def _dedupe_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []

    for product in products:
        if not isinstance(product, dict):
            continue

        key = _safe_text(
            product.get("url")
            or product.get("product_id")
            or product.get("sku")
            or product.get("name")
        )

        if not key or key in seen:
            continue

        seen.add(key)
        output.append(product)

    return output


def _extract_products(result: dict[str, Any]) -> list[dict[str, Any]]:
    products = result.get("products") or []

    if not isinstance(products, list):
        return []

    return [product for product in products if isinstance(product, dict)]


def _retrieve_products(module: Any, message: str, plan: dict[str, Any], memory: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    mode = _safe_text(plan.get("answer_mode"))
    product_query = _safe_text(plan.get("product_query")) or message
    desired_limit = _bounded_int(plan.get("desired_product_count"), 1, 1, 8)

    # The user's explicit identity outranks LLM rewrites and previous selections.
    # Exception: for availability of several explicitly requested products,
    # comparison_queries already contains the individual product identities.
    # Do not collapse the whole sentence into one identity query.
    comparison_queries = [
        _safe_text(query)
        for query in (plan.get("comparison_queries") or [])
        if _safe_text(query)
    ]

    multi_product_availability = (
        mode == "availability"
        and len(comparison_queries) > 1
    )

    explicit_query = (
        explicit_identity_query(message)
        if mode in {"single_product", "availability", "purchase"}
        and plan.get("price_intent", "none") == "none"
        and not multi_product_availability
        else None
    )

    if explicit_query:
        product_query = explicit_query
        plan["product_query"] = product_query

    if mode == "availability":
        products = []
        queries = [product_query] if explicit_query else comparison_queries or [product_query]
        remembered = None if explicit_query else plan.get("availability_products")
        if remembered is not None:
            cache = {}
            for p in remembered[:8]:
                url = p.get("url")
                if url not in cache:
                    cache[url] = check_live_availability(url)
                products.append({**p, **cache[url]})
        else:
            for query in dict.fromkeys(queries[:6]):
                try:
                    result = _call_mcp_tool(module, "lifestore_availability_lookup",
                                            product_query=query, requested_availability="in_stock")
                    for p in _extract_products(result)[:1]:
                        # Old/missing MCP implementations must never certify stored stock.
                        products.append(p if p.get("availability_verified") is True
                                        else {**p, **unverified("not_verified")})
                except Exception:
                    products.append({"name": query, **unverified("lookup_failed")})
        products = _dedupe_products(products)
        return {"products": products, "answer": "", "retrieval": {
            "returned_products": len(products)}}, products, "lifestore_availability_lookup"

    if plan.get("price_clarification"):
        return {"status": "success", "retrieval": {"returned_products": 0}}, [], None

    if plan.get("price_intent", "none") != "none":
        result = _call_mcp_tool(
            module, "lifestore_hybrid_product_search",
            query=product_query, product_query=product_query, search_mode="auto",
            limit=100, price_candidates=True, include_vector_evidence=False,
        )
        candidates = _dedupe_products(_extract_products(result))
        matches = _apply_price_constraints(candidates, plan)
        products = matches[:desired_limit]
        # Resolve deferred images only for the final cards, not 100 candidates.
        image_resolver = getattr(module, "_resolve_product_image", None)
        if callable(image_resolver):
            products = [
                {**p, **image_resolver(p.get("url") or "", p.get("image_url") or "", allow_page_fetch=False)}
                if p.get("image_source") == "deferred" else p
                for p in products
            ]
        result = dict(result)
        result["products"] = products
        result["status"] = "success" if products else "not_found"
        # Never reuse an MCP answer written about the unfiltered candidate pool.
        result["answer"] = ""
        result["retrieval"] = {
            **(result.get("retrieval") or {}),
            "price_candidate_count": len(candidates),
            "price_match_count": len(matches), "returned_products": len(products),
        }
        return result, products, "lifestore_hybrid_product_search"

    # Planner answer modes are not always valid MCP search_mode values.
    # For category/general browsing, let the MCP tool auto-detect the best retrieval path.
    retrieval_search_mode = (
        mode
        if mode in {"single_product", "availability", "comparison", "general", "auto"}
        else "auto"
    )

    if mode in {"single_product", "purchase"}:
        if not product_query.strip() or _LOW_SIGNAL_QUERY.match(product_query.strip()):
            # The resolved query has no real product-name content, whatever
            # wording produced it — don't waste a lookup call on garbage.
            state = (memory or {}).get("state") or {}
            fallback_category = state.get("current_category")
            if fallback_category:
                result = _call_mcp_tool(
                    module, "lifestore_hybrid_product_search",
                    query=fallback_category, search_mode="auto",
                    limit=desired_limit, include_vector_evidence=True,
                )
                return result, _dedupe_products(_extract_products(result))[:desired_limit], "lifestore_hybrid_product_search"
            return {"status": "not_found", "answer": CLARIFICATION}, [], None
        try:
            result = _call_mcp_tool(
                module,
                "lifestore_precise_product_lookup",
                product_query=product_query,
                include_vector_evidence=True,
            )
            return result, _extract_products(result)[:1], "lifestore_precise_product_lookup"
        except Exception:
            # A failed identity lookup must not substitute a top semantic hit.
            return {"status": "not_found", "answer": CLARIFICATION}, [], "lifestore_precise_product_lookup"

    if mode == "comparison":
        comparison_queries = plan.get("comparison_queries") or []
        try:
            result = _call_mcp_tool(
                module,
                "lifestore_compare_products",
                query=product_query or message,
                product_queries=comparison_queries,
                limit=max(desired_limit, 2),
                include_vector_evidence=True,
            )
            return result, _dedupe_products(_extract_products(result))[:desired_limit], "lifestore_compare_products"
        except Exception:
            result = _call_mcp_tool(
                module,
                "lifestore_hybrid_product_search",
                query=product_query or message,
                search_mode="comparison",
                limit=max(desired_limit, 2),
                include_vector_evidence=True,
            )
            return result, _dedupe_products(_extract_products(result))[:desired_limit], "lifestore_hybrid_product_search"

    result = _call_mcp_tool(
        module,
        "lifestore_hybrid_product_search",
        query=product_query or message,
        search_mode=retrieval_search_mode,
        limit=desired_limit,
        include_vector_evidence=True,
    )

    return result, _dedupe_products(_extract_products(result))[:desired_limit], "lifestore_hybrid_product_search"


@router.get("/mcp-health")
def lifestore_mcp_health() -> dict[str, Any]:
    """
    Browser/backend health check for the LifeStore MCP proxy.
    """
    server_path = _mcp_server_path()

    try:
        module = _load_mcp_module()

        return {
            "status": "ok",
            "mcp_server_path": str(server_path),
            "mcp_server_exists": server_path.exists(),
            "has_lifestore_hybrid_product_search": callable(getattr(module, "lifestore_hybrid_product_search", None)),
            "has_lifestore_precise_product_lookup": callable(getattr(module, "lifestore_precise_product_lookup", None)),
            "has_lifestore_availability_lookup": callable(getattr(module, "lifestore_availability_lookup", None)),
            "has_lifestore_compare_products": callable(getattr(module, "lifestore_compare_products", None)),
        }

    except Exception as error:
        return {
            "status": "error",
            "mcp_server_path": str(server_path),
            "mcp_server_exists": server_path.exists(),
            "message": str(error),
            "traceback": traceback.format_exc(),
        }


def _write_greeting_answer(message: str) -> str:
    """Friendly, product-free reply for greetings and small talk."""
    default_greeting = (
        "Hi! 👋 I'm Ask LifeStore. I can help you find products, check availability, "
        "compare items, or place an order. What are you looking for today?"
    )

    llm = _get_lifestore_openai_llm()
    if llm is None:
        return default_greeting

    try:
        response = llm.invoke(
            [
                (
                    "system",
                    (
                        "You are Ask LifeStore, a friendly shopping assistant. "
                        "The user sent a greeting, thanks, or small talk — not a product request. "
                        "Reply warmly in 1-2 short sentences. "
                        "Do NOT recommend, name, or describe any specific product. "
                        "Briefly mention you can help find products, check availability, compare items, "
                        "or place an order, and invite them to say what they are looking for."
                    ),
                ),
                ("human", message),
            ]
        )
        return _safe_text(getattr(response, "content", response)) or default_greeting
    except Exception:
        return default_greeting


def _order_status_response(message: str) -> dict[str, Any] | None:
    """Fail closed for explicit tracking requests: no order source is connected.

    An order reference is user input, never a product identity or a verified
    order. Keep this route independent of the planner and all product tools.
    """
    intent = re.search(
        r"\border\s+status\b"
        r"|\bstatus\s+(?:of|for)\s+(?:(?:my|the|an)\s+)?order\b"
        r"|\b(?:check|track|locate)\s+(?:(?:my|the|an)\s+)?order\b"
        r"|\bwhere\s+is\s+(?:(?:my|the)\s+)?order\b",
        message, re.I,
    )
    if not intent:
        return None

    tail = message[intent.end():].strip()
    tail = re.sub(r"^status\b\s*", "", tail, flags=re.I)
    # Preserve the supplied token, including #, case and leading zeros.
    tagged = re.search(r"#[^\s?!,;]+", tail)
    if tagged:
        reference = tagged.group(0)
    else:
        tail = re.sub(r"^(?:(?:for|number|reference|id|no)\b[.:]?\s*)*[:]?\s*",
                      "", tail, flags=re.I)
        reference = tail.split()[0].rstrip("?!,;") if tail else ""
        if reference.lower() in {"please", "yet", "now"}:
            reference = ""

    answer = "I can't verify that order reference from the currently connected order data."
    return {
        "status": "unavailable", "answer": answer, "reply": answer,
        "order": None, "order_reference": reference or None, "products": [],
        "tool_name": None, "form_payload": None,
        "answer_plan": {"answer_mode": "order_status", "show_product_cards": False,
                        "open_lifestore_form": False},
        "retrieval": {"answer_mode": "order_status", "returned_products": 0,
                      "retrieval_policy": "order_lookup_unavailable"},
        "frontend_contract": {"render_as": "assistant_answer", "answer_field": "answer",
                              "cards_field": "products", "form_payload_field": "form_payload"},
    }


def _split_order_and_shopping(message: str) -> tuple[str, str] | None:
    """Split only explicit tracking plus an independent actionable request.

    Internal color, comparison and price conjunctions remain intact. Ambiguous
    requests retain the authoritative CB-20 guard instead of reaching retrieval.
    """
    action = r"^(?:please\s+)?(?:show|find|list|recommend|compare|tell\s+me|do\s+you\s+have|is|are|can\s+i\s+buy|i\s+want\s+to\s+(?:buy|order|purchase))\b"
    for boundary in re.finditer(r"\s+and\s+(?:also\s+)?|\s*;\s*", message, re.I):
        left, right = message[:boundary.start()].strip(), message[boundary.end():].strip()
        left_order = _order_status_response(left)
        right_order = _order_status_response(right)
        if (left_order is None) == (right_order is None):
            continue
        shopping = left if left_order is None else right
        tracking = right if left_order is None else left
        # The tracking request must begin here, not occur after another clause.
        tracking_start = r"^(?:please\s+)?(?:check|track|locate|where\s+is|order\s+status|status\s+(?:of|for))\b"
        if re.search(action, shopping, re.I) and re.search(tracking_start, tracking, re.I):
            return left, right
    return None


@router.post("/mcp-chat")
def lifestore_mcp_chat(request: LifeStoreMCPChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    memory = get_conversation_memory(request.thread_id)
    save_message(request.thread_id, "user", message)
    clauses = _split_order_and_shopping(message)
    if clauses:
        responses = [_process_lifestore_clause(request, clause, memory) for clause in clauses]
        order = next(r for r in responses if r.get("answer_plan", {}).get("answer_mode") == "order_status")
        normal = next(r for r in responses if r is not order)
        answer = "\n\n".join(r["answer"] for r in responses)
        # Preserve normal cards, form and retrieval metadata even if tracking fails.
        response = {**normal, "answer": answer, "reply": answer,
                    "order": order["order"], "order_reference": order["order_reference"],
                    "order_status_result": order}
    else:
        response = _process_lifestore_clause(request, message, memory)
    save_message(request.thread_id, "assistant", response["answer"])
    if response.get("answer_plan", {}).get("answer_mode") not in {"order_status", "greeting"}:
        _maybe_summarize_conversation(request.thread_id)
    return response


def _process_lifestore_clause(request: LifeStoreMCPChatRequest, message: str,
                              memory: dict[str, Any]) -> dict[str, Any]:
    """Process one clause without splitting or recording internal messages."""
    try:
        order_response = _order_status_response(message)
        if order_response is not None:
            return order_response

        historical = historical_price_reference(message, memory)
        if historical is not None:
            # Recall uses persisted facts before LLM planning or identity extraction.
            # Do not turn old prices into live cards or replace the displayed list.
            return {
                "status": "success", "answer": historical["answer"],
                "reply": historical["answer"], "products": [], "tool_name": None,
                "form_payload": None,
                "answer_plan": {"answer_mode": "conversation_history",
                                "price_intent": historical["price_intent"],
                                "show_product_cards": False, "open_lifestore_form": False},
                "retrieval": {"source": "conversation_memory", "returned_products": 0,
                              "matched_product_count": historical["matched_product_count"],
                              "priced_product_count": historical["priced_product_count"]},
                "frontend_contract": {"render_as": "assistant_answer", "answer_field": "answer",
                                      "cards_field": "products", "form_payload_field": "form_payload"},
            }

        module = _load_mcp_module()
        plan = _plan_lifestore_answer(
            message=message,
            requested_limit=request.limit,
            conversation_memory=memory,
        )
        _resolve_price_context(plan, memory)
        _resolve_availability_context(plan, memory, message)

        answer_mode = _safe_text(
            plan.get("answer_mode")
        )


        # =====================================================
        # STEP 4
        # GREETING
        # =====================================================

        if answer_mode == "greeting":

            greeting_answer = _write_greeting_answer(
                message
            )

            return {
                "status": "success",
                "reply": greeting_answer,
                "answer": greeting_answer,
                "products": [],
                "retrieval": {
                    "answer_mode": "greeting",
                    "returned_products": 0,
                },
                "tool_name": None,
                "answer_plan": plan,
                "form_payload": None,
                "frontend_contract": {
                    "render_as": "assistant_answer",
                    "answer_field": "answer",
                    "cards_field": "products",
                    "form_payload_field": "form_payload",
                    "image_rule": (
                        "Render products[].image_url inside an img tag. "
                        "Do not show image URLs as plain text."
                    ),
                },
            }


        # =====================================================
        # STEP 5
        # RETRIEVE PRODUCTS
        # =====================================================

        result, products, tool_name = _retrieve_products(
            module,
            message,
            plan,
            memory,
        )


        # =====================================================
        # STEP 6
        # UPDATE STRUCTURED MEMORY
        # =====================================================

        if products:

            # Remember exactly which products were returned.
            save_products_shown(
                request.thread_id,
                products,
            )


            # If there is exactly one returned product,
            # that becomes the currently selected product.
            if len(products) == 1:

                set_last_selected_product(
                    request.thread_id,
                    products[0],
                )


            # Try to determine the current category.
            #
            # Prefer product_type because it is often cleaner:
            # router, speaker, camera...
            #
            # Fall back to category if product_type is missing.

            first_product = products[0]
            previous_state = memory.get("state") or {}
            previous_products = previous_state.get("last_products_shown") or []
            previous_selected = previous_state.get("last_selected_product")
            if previous_selected:
                previous_products = [*previous_products, previous_selected]
            same_context = any(
                (p.get("product_id") and p.get("product_id") == first_product.get("product_id"))
                or (p.get("name") and p.get("name") == first_product.get("name"))
                for p in previous_products
            )

            category = (
                _safe_text(
                    first_product.get("product_type")
                )
                or (_safe_text(plan.get("product_query")) if answer_mode == "category_browse" else "")
                or (_safe_text(previous_state.get("current_category")) if same_context else "")
                or _safe_text(
                    first_product.get("category")
                )
            )

            if category:
                set_current_category(
                    request.thread_id,
                    category,
                )


        # =====================================================
        # STEP 7
        # PREPARE FALLBACK ANSWER
        # =====================================================

        fallback_answer = str(
            result.get("answer")
            or result.get("reply")
            or result.get("message")
            or (
                "I could not find enough LifeStore data "
                "to answer that."
            )
        )


        # =====================================================
        # STEP 8
        # GENERATE ANSWER
        # =====================================================

        answer = _write_lifestore_answer(
            message=message,
            plan=plan,
            products=products,
            fallback=fallback_answer,
        )


        # =====================================================
        # STEP 9
        # FORM / PURCHASE STATE
        # =====================================================

        form_payload: dict[str, Any] | None = None

        if bool(
            plan.get("open_lifestore_form")
        ):

            form_payload = {
                "product": (
                    _safe_text(
                        products[0].get("name")
                    )
                    if products
                    else _safe_text(
                        plan.get("product_query")
                    )
                )
            }

            set_pending_action(
                request.thread_id,
                "purchase",
            )

            answer = (
                f"{answer}\n\n"
                "[RENDER_LIFESTORE_FORM]"
            )

        else:

            # No unfinished purchase action for normal queries.
            set_pending_action(
                request.thread_id,
                None,
            )


        # =====================================================
        # STEP 11
        # PRODUCT CARDS
        # =====================================================

        show_cards = bool(
            plan.get(
                "show_product_cards",
                True,
            )
        )

        if (
            answer_mode == "comparison"
            and not bool(
                plan.get(
                    "show_product_cards"
                )
            )
        ):
            show_cards = False


        response_products = (
            products
            if show_cards
            else []
        )


        # =====================================================
        # STEP 12
        # RESPONSE
        # =====================================================

        return {
            "status": str(
                result.get("status")
                or "success"
            ),

            "reply": answer,

            "answer": answer,

            "products": response_products,

            "retrieval": (
                result.get("retrieval")
                or {}
            ),

            "tool_name": tool_name,

            "answer_plan": plan,

            "form_payload": form_payload,

            "frontend_contract": {
                "render_as": (
                    "assistant_answer_with_product_cards"
                ),

                "answer_field": "answer",

                "cards_field": "products",

                "form_payload_field": (
                    "form_payload"
                ),

                "image_rule": (
                    "Render products[].image_url inside an img tag. "
                    "Do not show image URLs as plain text."
                ),
            },
        }


    except Exception as error:

        print("LifeStore MCP proxy failed:")
        print(traceback.format_exc())

        return {
            "status": "mcp_proxy_failed",

            "reply": (
                "Sorry, I could not connect to the "
                "LifeStore MCP proxy. "
                "Please check the FastAPI backend terminal logs."
            ),

            "answer": (
                "Sorry, I could not connect to the "
                "LifeStore MCP proxy. "
                "Please check the FastAPI backend terminal logs."
            ),

            "products": [],

            "retrieval": {},

            "tool_name": (
                "lifestore_hybrid_product_search"
            ),

            "error": str(error),

            "traceback": traceback.format_exc(),
        }
