"""Deterministic identity matching for precise lookups, not category search."""
import re
import unicodedata


CLARIFICATION = "I couldn't find an exact match for that product. Could you confirm the product name?"
_QUESTION_WORDS = set("""is are was were the a an how about what currently available availability
    in out of stock do does you have can i buy now please tell me more regarding
    im i'm m asking for it this that these those one ones product products its
    price of want to order purchase""".split())
_REFERENCES = re.compile(r"\b(this|that|these|those|it|its|first|second|third|fourth|fifth|sixth|seventh|eighth)\b", re.I)


def identity_tokens(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.findall(r"[^\W_]+", text, flags=re.UNICODE)


def clean_identity_query(value):
    return " ".join(t for t in identity_tokens(value) if t not in _QUESTION_WORDS)


def explicit_identity_query(message):
    """Protect literal names/models from planner replacement; keep references in memory."""
    if message.strip().startswith("https://"):
        return message.strip()
    tokens = clean_identity_query(message).split()
    has_model = any(any(c.isdigit() for c in t) and any(c.isalpha() for c in t) for t in tokens)
    if has_model or (len(tokens) >= 2 and not _REFERENCES.search(message)):
        return " ".join(tokens)
    return None


def identity_rank(query, product):
    """No description/category/vector scores may establish product identity."""
    if str(query).startswith("https://") and str(query).rstrip("/") == str(product.get("url") or "").rstrip("/"):
        return 4
    cleaned = clean_identity_query(query)
    tokens = set(identity_tokens(cleaned))
    if not tokens:
        return 0
    name = identity_tokens(product.get("name"))
    if "".join(identity_tokens(cleaned)) == "".join(name):
        return 3
    fields = [product.get(key) for key in ("product_id", "sku", "model", "model_number")]
    if any(value and "".join(identity_tokens(value)) == "".join(identity_tokens(cleaned)) for value in fields):
        return 2
    identity = set(name + identity_tokens(product.get("brand")))
    for value in fields:
        identity.update(identity_tokens(value))
    # ALL supplied terms, including brand/model, must occur in identity fields.
    # A model in a description (e.g. 'compatible with') is not a match.
    if not tokens <= identity:
        return 1 if _safe_wording_typo(tokens, name, product, identity) else 0
    if any(value and set(identity_tokens(value)) <= tokens for value in fields):
        return 2
    has_model = any(any(c.isdigit() for c in t) and any(c.isalpha() for c in t) for t in tokens)
    return 1 if has_model or len(tokens) >= 3 else 0


def _one_edit(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    short, long = sorted((left, right), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def _safe_wording_typo(tokens, name, product, identity):
    models = {t for t in tokens if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)}
    brand = set(identity_tokens(product.get("brand")))
    if not brand:
        # Catalog names conventionally start with the brand. Only infer this
        # prefix when an exact model token is also present in the name.
        index = next((i for i, t in enumerate(name) if t in models), 0)
        brand = set(name[:index])
    missing = tokens - identity
    if not models or not models <= identity or not brand or not brand <= tokens:
        return False
    # One descriptive word, one edit. Never repair a brand, model or number.
    if len(missing) != 1:
        return False
    word = next(iter(missing))
    return (word.isalpha() and len(word) >= 5 and any(
        t not in brand and t.isalpha() and len(t) >= 5 and _one_edit(word, t)
        for t in name))


def dedupe_identity_candidates(candidates):
    """Prefer earlier sources; never collapse variants with distinct product URLs."""
    unique = []
    def key(p):
        return (str(p.get("url") or "").rstrip("/"), str(p.get("product_id") or ""),
                str(p.get("sku") or ""),
                "".join(identity_tokens(p.get("name"))))
    for product in candidates:
        current = key(product)
        duplicate = False
        for previous in unique:
            other = key(previous)
            if current[0] and other[0]:
                duplicate = current[0] == other[0]
            else:
                duplicate = bool((current[1] and current[1] == other[1])
                                 or (current[2] and current[2] == other[2])
                                 or (current[3] and current[3] == other[3]))
            if duplicate:
                break
        if not duplicate:
            unique.append(product)
    return unique


def select_identity_product(query, candidates):
    ranked = [(identity_rank(query, p), p) for p in candidates]
    best = max((rank for rank, _ in ranked), default=0)
    matches = {}
    for rank, product in ranked:
        if rank and rank == best:
            key = product.get("url") or product.get("product_id") or product.get("name")
            matches.setdefault(key, product)
    # Never break an identity tie using semantic scores or catalog order.
    return next(iter(matches.values())) if len(matches) == 1 else None
