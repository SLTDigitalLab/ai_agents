"""Validated numeric prices and deterministic constraints for LifeStore results."""

import math
import re
from typing import Any


PRICE_INTENTS = {
    "none", "cheapest", "most_expensive", "under", "over", "between",
    "cheaper_than", "more_expensive_than",
}


def parse_price(value: Any) -> float | None:
    """Accept one nonnegative LKR amount, never extract digits from arbitrary text."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, str):
        value = re.sub(r"^(?:Rs\.?|LKR)\s*", "", value.strip(), flags=re.I)
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
            return None
        value = value.replace(",", "")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def product_price(product: dict[str, Any]) -> float | None:
    number = parse_price(product.get("price_value"))
    return number if number is not None else parse_price(product.get("price"))


def historical_price_reference(message: str, memory: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve explicit recall questions, never current catalog requests.

    The remembered list is a bounded subset, not a complete conversation archive.
    Include a separately selected product, but qualify answers to retained facts.
    Prose summaries are not used as a numeric product database.
    """
    match = re.fullmatch(
        r"\s*(?:(?:what|which)\s+(?:was|were|is|are)\s+|remind me(?: of)?\s+|show me\s+)?"
        r"(?:the\s+)?(?P<extreme>cheapest|most expensive)\s*"
        r"(?P<category>.*?)\s+(?:that\s+)?"
        r"(?:(?:we|you|I)\s+(?:previously\s+)?(?:discussed|mentioned|saw|viewed|looked at)"
        r"|you\s+(?:previously\s+)?showed(?:\s+me)?)"
        r"(?:\s+(?:earlier|before|previously|last time))?\s*[?.!]*\s*",
        message, re.I,
    )
    if not match:
        return None

    def words(value):
        # Category token normalization only; precise identity rules are untouched.
        return {w[:-1] if w.endswith('s') and not w.endswith('ss') else w
                for w in re.findall(r"\w+", str(value or '').casefold())}

    category = words(match['category']) - {'the', 'of', 'among', 'product', 'one'}
    state = memory.get('state') or {}
    candidates = list(state.get('last_products_shown') or [])
    selected = state.get('last_selected_product')
    if selected:
        candidates.append(selected)
    candidates = [p for p in candidates if p.get('name') and category <= words(
        ' '.join(str(p.get(k) or '') for k in ('name', 'product_type', 'category')))]
    priced = [p for p in candidates if product_price(p) is not None]
    intent = 'cheapest' if match['extreme'].casefold() == 'cheapest' else 'most_expensive'
    if not priced:
        answer = ("I don't have enough remembered product and price details to answer that "
                  "from our earlier conversation. Which products were we comparing?")
        winners = []
    else:
        target = (min if intent == 'cheapest' else max)(product_price(p) for p in priced)
        winners = [p for p in priced if product_price(p) == target]
        names = list(dict.fromkeys(p['name'] for p in winners))
        label = 'cheapest' if intent == 'cheapest' else 'most expensive'
        answer = (f"Among the matching products I still have saved from our conversation, "
                  f"the {label} {'was' if len(names) == 1 else 'were'} "
                  f"{', '.join(names)} at Rs. {target:,.2f}"
                  f"{' each' if len(names) > 1 else ''}. "
                  "That is the remembered price, not a current price check.")
        if len(priced) < len(candidates):
            answer += " Some remembered products have no saved price, so I could only compare those with prices."
    return {'answer': answer, 'price_intent': intent,
            'matched_product_count': len(candidates), 'priced_product_count': len(priced),
            'names': list(dict.fromkeys(p['name'] for p in winners))}


def apply_price_constraints(products: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return original dictionaries in deterministic order; missing bounds fail closed."""
    intent = plan.get("price_intent", "none")
    if intent not in PRICE_INTENTS or intent == "none":
        return list(products)
    low = parse_price(plan.get("min_price"))
    high = parse_price(plan.get("max_price"))
    reference = parse_price(plan.get("reference_price"))
    if low is not None and high is not None:
        low, high = sorted((low, high))
    priced = [(product, product_price(product)) for product in products]
    priced = [(product, price) for product, price in priced if price is not None]
    if intent == "under":
        priced = [(p, v) for p, v in priced if high is not None and v < high]
    elif intent == "over":
        priced = [(p, v) for p, v in priced if low is not None and v > low]
    elif intent == "between":
        priced = [(p, v) for p, v in priced if low is not None and high is not None and low <= v <= high]
    elif intent == "cheaper_than":
        priced = [(p, v) for p, v in priced if reference is not None and v < reference]
    elif intent == "more_expensive_than":
        priced = [(p, v) for p, v in priced if reference is not None and v > reference]
    if intent in {"cheapest", "most_expensive", "cheaper_than", "more_expensive_than"}:
        priced.sort(key=lambda item: item[1], reverse=intent == "most_expensive")
    return [product for product, _ in priced]
