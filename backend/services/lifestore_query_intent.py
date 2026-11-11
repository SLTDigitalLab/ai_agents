"""Small deterministic intent guards for unambiguous catalogue category requests."""
import re


_BROAD_CATEGORY_REQUEST = re.compile(
    r"\s*(?:(?:show|find|list|browse|give)(?:\s+me)?|tell\s+me\s+about)\s+"
    r"(?:the\s+)?(?P<category>(?:(?:wireless|wi-?fi|4g|network)\s+)*"
    r"(?:routers?|speakers?|phones?|telephones?|cameras?|headphones?|earphones?|"
    r"laptops?|tablets?|monitors?|printers?|extenders?|switches|access\s+points?|"
    r"power\s+banks?|chargers?|cables?))"
    r"(?:\s+(?:you\s+have|available|in\s+stock|in\s+the\s+catalog(?:ue)?))?"
    r"(?:\s+(?P<operator>under|below|over|above|less\s+than|more\s+than|"
    r"expensive\s+than|more\s+expensive\s+than|less\s+expensive\s+than|costlier\s+than)\s+"
    r"(?:Rs\.?\s*|LKR\s*)?(?P<amount>\d[\d, ]*(?:\.\d+)?))?\s*[?.!]*\s*",
    re.IGNORECASE,
)
_CATEGORY_PRICE_REQUEST = re.compile(
    r"\s*(?:(?:tell\s+me\s+about|what\s+is|what's|which\s+is|show\s+me|find|list(?:\s+me)?)\s+)?"
    r"(?:the\s+)?(?P<extreme>cheapest|cheapes|most\s+expensive|expensive|lowest\s+priced|lowest\s+price)\s+"
    r"(?P<category>(?:(?:wireless|wi-?fi|4g|network)\s+)*"
    r"(?:routers?|speakers?|phones?|telephones?|cameras?|headphones?|earphones?|"
    r"laptops?|tablets?|monitors?|printers?|extenders?|switches|access\s+points?|"
    r"power\s+banks?|chargers?|cables?))\s*[?.!]*\s*",
    re.IGNORECASE,
)
_ORDINAL_DETAILS = re.compile(
    r"\s*(?:(?:tell\s+me\s+about|what\s+about|show\s+me)\s+)?"
    r"(?:the\s+)?(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth)"
    r"\s+(?:one|product|router)\s*[?.!]*\s*",
    re.IGNORECASE,
)
_PRICE_OPERATOR = re.compile(
    r"\b(?P<operator>under|below|less\s+than|cheaper\s+than|"
    r"over|above|more\s+than|costlier\s+than|more\s+expensive\s+than)\s+"
    r"(?:Rs\.?\s*|LKR\s*)?(?P<amount>\d[\d, ]*(?:\.\d+)?)\b",
    re.IGNORECASE,
)

_PRICE_SUPERLATIVE = re.compile(
    r"\bcheapest\b|\bmost\s+expensive\b|\blowest\s+price(?:d)?\b|\bpriciest\b|\bcostliest\b"
    r"|\b(?<!more\s)expensive\b(?!\s+than)",
    re.IGNORECASE,
)

_KNOWN_CATEGORY_WORD = re.compile(
    r"\b(routers?|speakers?|phones?|telephones?|cameras?|headphones?|earphones?|"
    r"laptops?|tablets?|monitors?|printers?|extenders?|switches|access\s+points?|"
    r"power\s+banks?|chargers?|cables?)\b",
    re.IGNORECASE,
)
_WANTS_HIGHEST = re.compile(r"\b(expensive|priciest|costliest|highest)\b", re.IGNORECASE)

def _superlative_intent(message: str) -> str:
    return "most_expensive" if _WANTS_HIGHEST.search(message) else "cheapest"

def implicit_price_plan(message: str) -> dict | None:
    """Catch any price-cue phrasing that names no category/product, regardless of wording."""
    if _KNOWN_CATEGORY_WORD.search(message):
        return None  # a real category word is present — let the existing rules or LLM handle it

    bound = _PRICE_OPERATOR.search(message)
    if bound:
        operator = re.sub(r"\s+", " ", bound.group("operator").casefold())
        intent = "under" if operator in {
            "under", "below", "less than", "cheaper than"
        } else "over"
        amount = float(re.sub(r"[\s,]+", "", bound.group("amount")))
        return {"price_intent": intent, ("max_price" if intent == "under" else "min_price"): amount}

    if _PRICE_SUPERLATIVE.search(message):
        return {"price_intent": _superlative_intent(message), "desired_product_count": 1}
    return None


def recover_category_price_plan(message: str, current_category: str | None) -> dict | None:
    """Last-resort recovery — call this ONLY after an exact-product lookup has
    already come back empty, never as an upfront classifier.

    Unlike implicit_price_plan (which backs off the moment a category word is
    present, deferring to the anchored/verb-whitelisted functions below), this
    is meant to run when those have already been tried and the request is
    still unresolved. Because it only activates after a real attempt has
    failed, it's safe to be loose: it searches the raw message anywhere for a
    price signal, independent of the verb or phrasing the user used
    ("suggest me X", "yo gimme X", anything), and falls back to the
    conversation's current_category when the message itself names none.
    """
    category_match = _KNOWN_CATEGORY_WORD.search(message)
    category = category_match.group(0).strip() if category_match else (current_category or "").strip()
    if not category:
        return None

    bound = _PRICE_OPERATOR.search(message)
    if bound:
        operator = re.sub(r"\s+", " ", bound.group("operator").casefold())
        intent = "under" if operator in {
            "under", "below", "less than", "cheaper than"
        } else "over"
        amount = float(re.sub(r"[\s,]+", "", bound.group("amount")))
        return {
            "answer_mode": "category_browse", "product_query": category,
            "price_intent": intent,
            "min_price": amount if intent == "over" else None,
            "max_price": amount if intent == "under" else None,
            "desired_product_count": 5,
        }

    if _PRICE_SUPERLATIVE.search(message):
        return {
            "answer_mode": "category_browse", "product_query": category,
            "price_intent": _superlative_intent(message), "desired_product_count": 1,
        }

    return None


def _one_edit_apart(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) > len(right):
            i += 1
        elif len(right) > len(left):
            j += 1
        else:
            i += 1
            j += 1
    edits += (len(left) - i) + (len(right) - j)
    return edits <= 1


def broad_category_query(message: str) -> str | None:
    """Return a literal category only for clear browse requests, never model names."""
    match = _broad_category_match(message)
    return match.group("category").strip() if match else None


def _broad_category_match(message: str):
    match = _BROAD_CATEGORY_REQUEST.fullmatch(message)
    if match is None:
        prefixes = (
            ("tell", "me", "about"), ("show", "me"), ("give", "me"),
            ("list", "me"), ("tell", "me"), ("show",), ("give",),
            ("find",), ("list",), ("browse",),
        )
        words = message.strip().split()
        for expected in prefixes:
            if len(words) <= len(expected):
                continue
            actual = words[:len(expected)]
            if all(_one_edit_apart(left.casefold(), right) for left, right in zip(actual, expected)):
                normalized = " ".join((*expected, *words[len(expected):]))
                match = _BROAD_CATEGORY_REQUEST.fullmatch(normalized)
                if match:
                    break
    return match


def category_price_intent(message: str) -> tuple[str, str] | None:
    """Return a category and rank intent for explicit cheapest/expensive browsing."""
    match = _CATEGORY_PRICE_REQUEST.fullmatch(message)
    if match is None:
        return None
    extreme = re.sub(r"\s+", " ", match.group("extreme").casefold())
    price_intent = "most_expensive" if extreme in {"most expensive", "expensive"} else "cheapest"
    return match.group("category").strip(), price_intent


def category_price_bound(message: str) -> tuple[str, str, float] | None:
    """Return a category, under/over intent, and literal amount from a clear browse request."""
    match = _broad_category_match(message)
    if match is None or not match.group("operator") or not match.group("amount"):
        return None
    operator = re.sub(r"\s+", " ", match.group("operator").casefold())
    intent = "under" if operator in {
        "under", "below", "less than", "less expensive than"
    } else "over"
    amount_text = re.sub(r"[\s,]+", "", match.group("amount"))
    amount = float(amount_text)
    return match.group("category").strip(), intent, amount


def remembered_ordinal_query(message: str, memory: dict) -> str | None:
    """Resolve a clear ordinal details request against the displayed product order."""
    match = _ORDINAL_DETAILS.fullmatch(message)
    if match is None or not isinstance(memory, dict):
        return None
    positions = {name: index for index, name in enumerate((
        "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"), 1)}
    products = ((memory.get("state") or {}).get("last_products_shown") or [])
    position = positions[match.group("ordinal").casefold()]
    if not isinstance(products, list) or position > len(products):
        return None
    product = products[position - 1]
    if not isinstance(product, dict):
        return None
    query = product.get("name") or product.get("product_id") or product.get("url")
    return query.strip() if isinstance(query, str) and query.strip() else None