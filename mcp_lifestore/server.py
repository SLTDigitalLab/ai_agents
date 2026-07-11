from __future__ import annotations

import json
import csv
import os
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP  # type: ignore

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)

mcp = FastMCP(
    "Ask LifeStore MCP",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8001")),
    stateless_http=True,
    json_response=True,
)


class MCPBearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Bearer-token authentication for LifeStore MCP Streamable HTTP.

    If LIFESTORE_MCP_TOKEN is set, every HTTP request to the MCP server must include:

        Authorization: Bearer <token>

    If LIFESTORE_MCP_TOKEN is not set, authentication is skipped. This keeps local
    development flexible while allowing secured staging/production deployments.
    """

    async def dispatch(self, request: Request, call_next):
        expected_token = os.getenv("LIFESTORE_MCP_TOKEN", "").strip()

        if not expected_token:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "").strip()
        expected_header = f"Bearer {expected_token}"

        if auth_header != expected_header:
            return JSONResponse(
                {
                    "error": "Unauthorized MCP request",
                    "detail": "Missing or invalid bearer token.",
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ADMIN_BASE_URL = os.getenv("ADMIN_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_USER_EMAIL = os.getenv("ADMIN_USER_EMAIL", "admin@slt.lk")

LIFESTORE_SOURCE_URL = os.getenv(
    "LIFESTORE_SOURCE_URL",
    "https://lifestore.lk/products?categories=All&products",
)
LIFESTORE_AGENT_NAME = os.getenv("LIFESTORE_AGENT_NAME", "ask_lifestore")
LIFESTORE_QDRANT_COLLECTION = os.getenv("LIFESTORE_QDRANT_COLLECTION", "lifestore_docs")

LIFESTORE_INGEST_MAX_PAGES = int(os.getenv("LIFESTORE_INGEST_MAX_PAGES", "1000"))
LIFESTORE_INGEST_MAX_DEPTH = int(os.getenv("LIFESTORE_INGEST_MAX_DEPTH", "5"))

ALLOW_REFRESH = os.getenv("ALLOW_REFRESH", "false").lower() == "true"

PRODUCTS_JSON_ENV = os.getenv(
    "LIFESTORE_PRODUCTS_JSON",
    "backend/data/real/lifestore_all.json",
)
PRODUCTS_JSON_PATH = PROJECT_ROOT / PRODUCTS_JSON_ENV

NEO4J_URI = os.getenv("NEO4J_URI", "").strip()
NEO4J_USER = (
    os.getenv("NEO4J_USER")
    or os.getenv("NEO4J_USERNAME")
    or "neo4j"
).strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()

ORDERS_DIR = Path(__file__).parent / "orders"
ORDERS_FILE = ORDERS_DIR / "local_draft_orders.json"


def load_products() -> list[dict[str, Any]]:
    if not PRODUCTS_JSON_PATH.exists():
        return []

    with PRODUCTS_JSON_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["products", "items", "data", "results"]:
            value = data.get(key)
            if isinstance(value, list):
                return value

    return []


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def product_field(
    product: dict[str, Any],
    possible_keys: list[str],
    default: str = "",
) -> str:
    for key in possible_keys:
        value = safe_text(product.get(key))
        if value:
            return value
    return default


def normalize_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": product_field(product, ["product_id", "id", "sku", "ProductId"]),
        "sku": product_field(product, ["sku", "SKU"]),
        "name": product_field(product, ["name", "title", "product_name", "ProductName"]),
        "brand": product_field(product, ["brand", "brand_name", "Brand"]),
        "category": product_field(product, ["category", "category_name", "Category"]),
        "price": product_field(product, ["price", "selling_price", "Price"]),
        "price_value": product.get("price_value"),
        "currency": product_field(product, ["currency"], "LKR"),
        "availability": product_field(
            product,
            ["availability", "stock_status", "status", "Availability"],
        ),
        "stock": product.get("stock"),
        "url": product_field(product, ["url", "link", "product_url"]),
        "image_url": product_field(product, ["image_url", "image", "thumbnail"]),
        "product_type": product_field(product, ["product_type", "type"]),
        "seller": product_field(product, ["seller"]),
        "tags": product.get("tags", []),
        "description": product_field(product, ["description", "short_description"]),
        "raw": product,
    }


def parse_json_string_if_needed(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        return value

    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return value

    try:
        return json.loads(stripped)
    except Exception:
        return value


def keyword_score(product: dict[str, Any], query: str) -> int:
    query = query.lower().strip()
    tokens = query.split()

    name = safe_text(product.get("name")).lower()
    brand = safe_text(product.get("brand")).lower()
    category = safe_text(product.get("category")).lower()
    product_type = safe_text(product.get("product_type")).lower()
    description = safe_text(product.get("description")).lower()

    tags = product.get("tags", [])
    if isinstance(tags, list):
        tags_text = " ".join(str(tag) for tag in tags).lower()
    else:
        tags_text = safe_text(tags).lower()

    score = 0

    if query in name:
        score += 20

    if query == product_type:
        score += 18

    if query in tags_text:
        score += 15

    if query in category:
        score += 10

    if query in brand:
        score += 5

    if query in description:
        score += 2

    for token in tokens:
        if token in name:
            score += 5
        if token in product_type:
            score += 4
        if token in tags_text:
            score += 4
        if token in category:
            score += 2
        if token in description:
            score += 1

    return score


def product_matches_availability(product: dict[str, Any], availability: str | None) -> bool:
    if not availability:
        return True

    availability = availability.lower().strip()

    stock_status = safe_text(product.get("availability")).lower()
    stock_value = product.get("stock")

    is_in_stock = stock_status in {"in_stock", "in stock", "available"} or stock_value == 1
    is_out_of_stock = stock_status in {"out_of_stock", "out of stock", "unavailable"} or stock_value == 0

    if availability in {"in_stock", "in stock", "available"}:
        return is_in_stock

    if availability in {"out_stock", "out_of_stock", "out of stock", "unavailable"}:
        return is_out_of_stock

    return True



CATEGORY_ALIASES = {
    "special offer": "Special Offers",
    "special offers": "Special Offers",
    "specially offered": "Special Offers",
    "specially-offered": "Special Offers",
    "specially offered items": "Special Offers",
    "specially-offered items": "Special Offers",
    "offer items": "Special Offers",
    "offered items": "Special Offers",
    "new connection": "New Connection Offers",
    "new connection offer": "New Connection Offers",
    "new connection offers": "New Connection Offers",
    "bundle offer": "Bundle Offers",
    "bundle offers": "Bundle Offers",
    "smart home corner": "Smart Home Corner",
}


def normalize_space_lower(value: Any) -> str:
    return re.sub(r"\s+", " ", safe_text(value).lower()).strip()


# ---------------------------------------------------------------------
# Strict MCP input validation
# ---------------------------------------------------------------------
# These helpers validate tool inputs before the MCP server uses them.
# The goal is not to decide whether a product exists; search/retrieval does that.
# The goal is to reject unsafe, oversized, or unexpected values at the tool boundary.

MAX_QUERY_LENGTH = int(os.getenv("LIFESTORE_MAX_QUERY_LENGTH", "240"))
MAX_CATEGORY_LENGTH = int(os.getenv("LIFESTORE_MAX_CATEGORY_LENGTH", "120"))
MAX_PRODUCT_QUERY_LENGTH = int(os.getenv("LIFESTORE_MAX_PRODUCT_QUERY_LENGTH", "240"))
MAX_COMPARE_ITEMS = int(os.getenv("LIFESTORE_MAX_COMPARE_ITEMS", "6"))
MAX_PRICE_VALUE = float(os.getenv("LIFESTORE_MAX_PRICE_VALUE", "10000000"))

ALLOWED_SORT_VALUES = {
    "relevance",
    "price_asc",
    "price_desc",
    "price-low-high",
    "price-high-low",
    "low_to_high",
    "high_to_low",
    "name_asc",
    "name",
}

ALLOWED_SEARCH_MODES = {
    "auto",
    "general",
    "single_product",
    "availability",
    "purchase",
    "exact",
    "comparison",
    "category",
}

AVAILABILITY_ALIASES = {
    "in_stock": "in_stock",
    "in stock": "in_stock",
    "available": "in_stock",
    "yes": "in_stock",
    "out_stock": "out_of_stock",
    "out_of_stock": "out_of_stock",
    "out of stock": "out_of_stock",
    "unavailable": "out_of_stock",
    "sold out": "out_of_stock",
    "no": "out_of_stock",
}

SUSPICIOUS_TEXT_MARKERS = (
    "\x00",
    "../",
    "..\\",
    "file://",
    "javascript:",
    "data:text",
    "data:application",
    "<script",
    "</script",
    "<?php",
    "$(",
    "`",
)


def invalid_input_response(field: str, message: str) -> dict[str, Any]:
    return {
        "status": "invalid_input",
        "message": f"Invalid {field}: {message}",
        "products": [],
    }


def validate_text_input(
    value: Any,
    field_name: str,
    *,
    max_length: int,
    allow_blank: bool = False,
    allow_lifestore_product_url: bool = True,
) -> tuple[str, dict[str, Any] | None]:
    text = safe_text(value)

    if not text:
        if allow_blank:
            return "", None
        return "", invalid_input_response(field_name, "value cannot be empty.")

    if len(text) > max_length:
        return "", invalid_input_response(
            field_name,
            f"value is too long. Maximum allowed length is {max_length} characters.",
        )

    lowered = text.lower()

    for marker in SUSPICIOUS_TEXT_MARKERS:
        if marker in lowered:
            return "", invalid_input_response(
                field_name,
                f"value contains unsupported pattern: {marker}",
            )

    # Product lookup can accept a LifeStore product URL, but not arbitrary URLs.
    if lowered.startswith(("http://", "https://")):
        if not allow_lifestore_product_url:
            return "", invalid_input_response(field_name, "URLs are not allowed here.")

        if "lifestore.lk/product/" not in lowered:
            return "", invalid_input_response(
                field_name,
                "only LifeStore product URLs are allowed.",
            )

    return text, None


def bounded_int(
    value: Any,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    try:
        number = int(value)
    except Exception:
        number = default

    return max(min(number, max_value), min_value)


def validate_price_value(
    value: Any,
    field_name: str,
) -> tuple[float | None, dict[str, Any] | None]:
    if value is None or safe_text(value) == "":
        return None, None

    try:
        number = float(value)
    except Exception:
        return None, invalid_input_response(field_name, "must be a valid number.")

    if number < 0:
        return None, invalid_input_response(field_name, "cannot be negative.")

    if number > MAX_PRICE_VALUE:
        return None, invalid_input_response(
            field_name,
            f"cannot exceed {MAX_PRICE_VALUE}.",
        )

    return number, None


def validate_price_range(
    min_price: Any,
    max_price: Any,
) -> tuple[float | None, float | None, dict[str, Any] | None]:
    cleaned_min, error = validate_price_value(min_price, "min_price")
    if error:
        return None, None, error

    cleaned_max, error = validate_price_value(max_price, "max_price")
    if error:
        return None, None, error

    if cleaned_min is not None and cleaned_max is not None and cleaned_min > cleaned_max:
        return None, None, invalid_input_response(
            "price_range",
            "min_price cannot be greater than max_price.",
        )

    return cleaned_min, cleaned_max, None


def normalize_sort_value(sort: Any) -> tuple[str, dict[str, Any] | None]:
    value = safe_text(sort).lower() or "relevance"

    if value not in ALLOWED_SORT_VALUES:
        return "relevance", invalid_input_response(
            "sort",
            f"unsupported sort value '{value}'.",
        )

    return value, None


def normalize_search_mode(search_mode: Any) -> tuple[str, dict[str, Any] | None]:
    value = safe_text(search_mode).lower() or "auto"

    if value not in ALLOWED_SEARCH_MODES:
        return "auto", invalid_input_response(
            "search_mode",
            f"unsupported search mode '{value}'.",
        )

    return value, None


def normalize_requested_availability(
    requested_availability: Any,
) -> tuple[str, dict[str, Any] | None]:
    value = normalize_space_lower(requested_availability or "in_stock")

    if value not in AVAILABILITY_ALIASES:
        return "in_stock", invalid_input_response(
            "requested_availability",
            f"unsupported availability value '{value}'.",
        )

    return AVAILABILITY_ALIASES[value], None


def validate_product_query_list(
    value: Any,
) -> tuple[list[str], dict[str, Any] | None]:
    parsed = parse_json_string_if_needed(value)

    if parsed is None or parsed == "":
        return [], None

    if isinstance(parsed, str):
        raw_items = [item for item in parsed.split(",")]
    elif isinstance(parsed, list):
        raw_items = parsed
    else:
        return [], invalid_input_response(
            "product_queries",
            "must be a list or comma-separated string.",
        )

    if len(raw_items) > MAX_COMPARE_ITEMS:
        return [], invalid_input_response(
            "product_queries",
            f"too many products. Maximum allowed is {MAX_COMPARE_ITEMS}.",
        )

    cleaned_items: list[str] = []

    for item in raw_items:
        cleaned, error = validate_text_input(
            item,
            "product_queries item",
            max_length=MAX_PRODUCT_QUERY_LENGTH,
            allow_blank=True,
            allow_lifestore_product_url=True,
        )
        if error:
            return [], error

        if cleaned:
            cleaned_items.append(cleaned)

    return cleaned_items, None


def canonical_category_query(query: str) -> str:
    value = normalize_space_lower(query)

    for alias, category in CATEGORY_ALIASES.items():
        if alias in value:
            return category

    return safe_text(query)


def _flatten_category_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_flatten_category_values(item))
        return output
    if isinstance(value, dict):
        output: list[str] = []
        for item in value.values():
            output.extend(_flatten_category_values(item))
        return output
    return [safe_text(value)] if safe_text(value) else []


def product_category_values(product: dict[str, Any]) -> list[str]:
    values: list[str] = []

    for key in [
        "category",
        "category_name",
        "categories",
        "category_path",
        "category_tree",
        "breadcrumbs",
        "tags",
    ]:
        values.extend(_flatten_category_values(product.get(key)))

    raw_product = product.get("raw")
    if isinstance(raw_product, dict):
        for key in [
            "category",
            "category_name",
            "categories",
            "category_path",
            "category_tree",
            "breadcrumbs",
            "tags",
        ]:
            values.extend(_flatten_category_values(raw_product.get(key)))

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_space_lower(value)
        if key and key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped


def product_has_exact_category(product: dict[str, Any], category: str) -> bool:
    wanted = normalize_space_lower(category)
    if not wanted:
        return False

    for value in product_category_values(product):
        normalized = normalize_space_lower(value)
        if normalized == wanted:
            return True

        parts = [
            normalize_space_lower(part)
            for part in re.split(r"[|>/,]+", value)
            if normalize_space_lower(part)
        ]
        if wanted in parts:
            return True

    return False


# ---------------------------------------------------------------------
# Generic product-family filters
# ---------------------------------------------------------------------
# Keyword/semantic search can match support/accessory products because their
# descriptions mention the main product. Example: a UPS may mention "for
# routers", so it can appear for "show me routers". These helpers detect main
# product-family queries and clean the product list before the LLM writes the
# visible table or the frontend renders product cards.

ACCESSORY_INTENT_MARKERS = {
    "accessory", "accessories", "spare", "spares", "part", "parts",
    "charger", "charging", "cable", "adapter", "adaptor", "converter",
    "cover", "case", "screen protector", "protector", "antenna",
    "extender", "repeater", "ups", "power backup", "backup power",
    "power tank", "battery backup", "battery", "power bank", "stand",
    "holder", "mount", "remote", "plug", "socket", "strap", "band",
    "bag", "sleeve", "keyboard", "mouse", "memory card", "sd card",
    "toner", "ink", "cartridge", "paper", "headset", "earphone", "earphones",
}

# Conservative rules for main-product queries. Exact category browsing should
# still use lifestore_strict_category_products; these rules solve cases where a
# family query such as "routers" or "phones" should not show accessories.
PRODUCT_FAMILY_RULES: dict[str, dict[str, Any]] = {
    "router": {
        "triggers": [
            "router", "routers", "adsl router", "adsl routers", "4g router",
            "4g routers", "mobile router", "mobile routers", "mi-fi", "mifi",
        ],
        "include_product_types": ["router"],
        "include_category_markers": [
            "routers", "adsl routers", "3g & 4g routers", "3g and 4g routers",
            "4g routers", "mobile routers",
        ],
        "include_name_markers": ["router", "mi-fi", "mifi", "cpe"],
        "exclude_markers": [
            "antenna", "external antenna", "extender", "wi-fi extender",
            "wifi extender", "repeater", "ups", "power backup", "backup power",
            "power tank", "powerlast", "battery backup", "charger", "adapter",
            "adaptor", "cable", "telephone line", "connectivity pack",
        ],
    },
    "phone": {
        "triggers": [
            "phone", "phones", "mobile phone", "mobile phones", "smartphone",
            "smartphones", "iphone", "iphones",
        ],
        "include_product_types": ["phone", "mobile phone", "smartphone"],
        "include_category_markers": ["mobile phones", "smartphones", "smart phones"],
        "include_name_markers": ["phone", "smartphone", "iphone", "galaxy"],
        "exclude_markers": [
            "charger", "charging", "cable", "adapter", "adaptor", "case", "cover",
            "screen protector", "protector", "earphone", "earphones", "headset",
            "power bank", "battery", "sim", "holder", "mount", "stand",
        ],
    },
    "tablet": {
        "triggers": ["tablet", "tablets", "tab", "tabs"],
        "include_product_types": ["tablet", "tab"],
        "include_category_markers": ["tablet", "tablets"],
        "include_name_markers": ["tablet", " tab"],
        "exclude_markers": [
            "charger", "cable", "adapter", "adaptor", "case", "cover",
            "screen protector", "keyboard", "stylus", "pen",
        ],
    },
    "laptop": {
        "triggers": ["laptop", "laptops", "notebook", "notebooks"],
        "include_product_types": ["laptop", "notebook"],
        "include_category_markers": ["laptop", "laptops", "notebook"],
        "include_name_markers": ["laptop", "notebook"],
        "exclude_markers": [
            "charger", "adapter", "adaptor", "bag", "case", "cover", "stand",
            "sleeve", "keyboard", "mouse", "cooler", "cooling pad", "dock", "cable",
        ],
    },
    "camera": {
        "triggers": ["camera", "cameras", "webcam", "webcams", "cctv", "ip camera"],
        "include_product_types": ["camera", "webcam", "cctv", "ip camera"],
        "include_category_markers": ["camera", "cameras", "webcam", "cctv"],
        "include_name_markers": ["camera", "webcam", "cctv", "cam"],
        "exclude_markers": [
            "memory card", "sd card", "cable", "adapter", "adaptor", "mount",
            "stand", "tripod", "case", "cover",
        ],
    },
    "speaker": {
        "triggers": ["speaker", "speakers", "smart speaker", "bluetooth speaker"],
        "include_product_types": ["speaker", "smart speaker", "bluetooth speaker"],
        "include_category_markers": ["speaker", "speakers"],
        "include_name_markers": ["speaker", "echo dot"],
        "exclude_markers": ["charger", "cable", "adapter", "adaptor", "stand", "mount", "case", "cover"],
    },
    "watch": {
        "triggers": ["watch", "watches", "smart watch", "smartwatch", "smartwatches"],
        "include_product_types": ["watch", "smart watch", "smartwatch"],
        "include_category_markers": ["watch", "watches", "smartwatch"],
        "include_name_markers": ["watch", "smartwatch"],
        "exclude_markers": ["strap", "band", "charger", "cable", "protector", "case", "cover"],
    },
    "tv": {
        "triggers": ["tv", "tvs", "television", "televisions", "smart tv", "smart tvs"],
        "include_product_types": ["tv", "television", "smart tv"],
        "include_category_markers": ["tv", "television", "smart tv"],
        "include_name_markers": [" tv", "television"],
        "exclude_markers": ["remote", "wall mount", "mount", "stand", "cable", "adapter", "adaptor"],
    },
    "printer": {
        "triggers": ["printer", "printers"],
        "include_product_types": ["printer"],
        "include_category_markers": ["printer", "printers"],
        "include_name_markers": ["printer"],
        "exclude_markers": ["toner", "ink", "cartridge", "paper", "cable", "adapter", "adaptor"],
    },
}


def _contains_marker(value: str, marker: str) -> bool:
    value = normalize_space_lower(value)
    marker = normalize_space_lower(marker)
    if not value or not marker:
        return False

    if re.fullmatch(r"[a-z0-9]+", marker):
        return re.search(rf"\b{re.escape(marker)}s?\b", value) is not None

    return marker in value


def _contains_any_marker(value: str, markers: Any) -> bool:
    if not markers:
        return False
    return any(_contains_marker(value, safe_text(marker)) for marker in markers)


def _query_requests_accessory_or_support(query: Any, category: Any = None) -> bool:
    value = normalize_space_lower(f"{safe_text(query)} {safe_text(category)}")
    return _contains_any_marker(value, ACCESSORY_INTENT_MARKERS)


def _combined_product_text(product: dict[str, Any], *, include_description: bool = False) -> str:
    parts: list[Any] = [
        product.get("name"), product.get("title"), product.get("product_name"),
        product.get("product_type"), product.get("type"), product.get("category"),
        product.get("category_name"),
    ]

    for value in product_category_values(product):
        parts.append(value)

    tags = product.get("tags")
    if isinstance(tags, list):
        parts.extend(tags)
    elif tags:
        parts.append(tags)

    if include_description:
        parts.append(product.get("description"))
        parts.append(product.get("short_description"))

    raw_product = product.get("raw")
    if isinstance(raw_product, dict):
        for key in [
            "name", "title", "product_name", "product_type", "type", "category",
            "category_name", "categories", "category_path", "category_tree",
            "breadcrumbs", "tags",
        ]:
            value = raw_product.get(key)
            if isinstance(value, list):
                parts.extend(value)
            elif value:
                parts.append(value)

    return normalize_space_lower(" ".join(safe_text(part) for part in parts if safe_text(part)))


def _family_requested_by_query(query: Any, category: Any = None) -> str | None:
    """
    Detect main product-family intent.

    Accessory/support intent disables strict family filtering so queries like
    "router power backup" or "phone charger" still return the accessory products.
    """
    value = normalize_space_lower(f"{safe_text(query)} {safe_text(category)}")
    if not value:
        return None

    if _query_requests_accessory_or_support(query, category):
        return None

    matched: list[tuple[int, str]] = []
    for family_name, rule in PRODUCT_FAMILY_RULES.items():
        for trigger in rule.get("triggers", []):
            trigger_text = safe_text(trigger)
            if _contains_marker(value, trigger_text):
                matched.append((len(trigger_text), family_name))
                break

    if not matched:
        return None

    matched.sort(reverse=True)
    return matched[0][1]


def product_belongs_to_family(product: dict[str, Any], rule: dict[str, Any]) -> bool:
    if not isinstance(product, dict):
        return False

    main_text = _combined_product_text(product, include_description=False)
    if _contains_any_marker(main_text, rule.get("exclude_markers") or []):
        return False

    name = normalize_space_lower(product.get("name") or product.get("title") or product.get("product_name"))
    product_type = normalize_space_lower(product.get("product_type") or product.get("type"))
    category = normalize_space_lower(product.get("category") or product.get("category_name"))
    category_values = " ".join(normalize_space_lower(value) for value in product_category_values(product))

    if product_type and _contains_any_marker(product_type, rule.get("include_product_types") or []):
        return True

    if category and _contains_any_marker(category, rule.get("include_category_markers") or []):
        return True

    if category_values and _contains_any_marker(category_values, rule.get("include_category_markers") or []):
        return True

    if name and _contains_any_marker(name, rule.get("include_name_markers") or []):
        return True

    return False


def filter_products_for_query_family(
    products: list[dict[str, Any]],
    query: Any,
    category: Any = None,
) -> list[dict[str, Any]]:
    """Apply strict product-family filters only when the user query needs them."""
    family = _family_requested_by_query(query, category)
    if not family:
        return products

    rule = PRODUCT_FAMILY_RULES.get(family)
    if not rule:
        return products

    return [product for product in products if product_belongs_to_family(product, rule)]


def query_family_filter_policy(query: Any, category: Any = None, default: str = "keyword_product_search") -> str:
    family = _family_requested_by_query(query, category)
    if not family:
        return default
    return f"strict_product_family_filter:{family}"


# Backward-compatible helpers retained for any existing tests/imports.
def _query_requests_router_family(query: Any, category: Any = None) -> bool:
    return _family_requested_by_query(query, category) == "router"


def is_strict_router_product(product: dict[str, Any]) -> bool:
    return product_belongs_to_family(product, PRODUCT_FAMILY_RULES["router"])


def load_local_orders() -> list[dict[str, Any]]:
    if not ORDERS_FILE.exists():
        return []

    with ORDERS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    return []


def save_local_order(order: dict[str, Any]) -> None:
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)
    orders = load_local_orders()
    orders.append(order)

    with ORDERS_FILE.open("w", encoding="utf-8") as file:
        json.dump(orders, file, indent=2, ensure_ascii=False)


def find_product_by_id_or_name(product_id_or_name: str) -> dict[str, Any] | None:
    products = [normalize_product(product) for product in load_products()]
    lookup = product_id_or_name.lower().strip()

    for product in products:
        possible_values = [
            safe_text(product.get("product_id")),
            safe_text(product.get("sku")),
            safe_text(product.get("name")),
            safe_text(product.get("url")),
        ]

        for value in possible_values:
            if value and value.lower().strip() == lookup:
                return product

    for product in products:
        name = safe_text(product.get("name")).lower()
        product_id = safe_text(product.get("product_id")).lower()

        if lookup in name or lookup in product_id:
            return product

    return None

def neo4j_driver():
    if GraphDatabase is None:
        return None

    if not NEO4J_URI or not NEO4J_USER or not NEO4J_PASSWORD:
        return None

    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )


def node_display_name(node_props: dict[str, Any]) -> str:
    for key in ["name", "title", "product_name", "brand", "category", "status", "product_id"]:
        value = safe_text(node_props.get(key))
        if value:
            return value
    return "Unknown"

def make_json_safe(value: Any) -> Any:
    """
    Convert Neo4j/Python special values into JSON-safe values for MCP responses.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]

    # Neo4j temporal values often support to_native()
    if hasattr(value, "to_native") and callable(value.to_native):
        try:
            return make_json_safe(value.to_native())
        except Exception:
            pass

    # Python datetime/date/time values support isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass

    # Some Neo4j values support iso_format()
    if hasattr(value, "iso_format") and callable(value.iso_format):
        try:
            return value.iso_format()
        except Exception:
            pass

    return str(value)


@mcp.tool()
def health_check() -> dict[str, Any]:
    """
    Check whether the Ask LifeStore MCP server is running.
    """
    return {
        "status": "ok",
        "server": "Ask LifeStore MCP",
        "source_url": LIFESTORE_SOURCE_URL,
        "agent_name": LIFESTORE_AGENT_NAME,
        "collection_name": LIFESTORE_QDRANT_COLLECTION,
        "products_json_path": str(PRODUCTS_JSON_PATH),
        "products_json_exists": PRODUCTS_JSON_PATH.exists(),
        "image_lookup_json_path": str(IMAGE_LOOKUP_JSON_PATH),
        "image_lookup_json_exists": IMAGE_LOOKUP_JSON_PATH.exists(),
        "local_orders_file": str(ORDERS_FILE),
    }


@mcp.tool()
def lifestore_search_products(
    q: str,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
    sort: str = "relevance",
    limit: int = 10,
    cursor: int = 0,
    currency: str = "LKR",
) -> dict[str, Any]:
    """
    Search LifeStore products by keyword with category, price range, stock, sort, limit, cursor, and currency filters.

    This is the LifeStore equivalent of Kapruka's product search tool.
    """
    parsed_q = parse_json_string_if_needed(q)
    if isinstance(parsed_q, dict):
        q = str(parsed_q.get("q", parsed_q.get("query", q)))
        category = parsed_q.get("category", category)
        min_price = parsed_q.get("min_price", min_price)
        max_price = parsed_q.get("max_price", max_price)
        in_stock_only = bool(parsed_q.get("in_stock_only", in_stock_only))
        sort = str(parsed_q.get("sort", sort))
        limit = parsed_q.get("limit", limit)
        cursor = parsed_q.get("cursor", cursor)
        currency = str(parsed_q.get("currency", currency))

    q, error = validate_text_input(
        q,
        "q",
        max_length=MAX_QUERY_LENGTH,
        allow_blank=True,
        allow_lifestore_product_url=False,
    )
    if error:
        return error

    category, error = validate_text_input(
        category,
        "category",
        max_length=MAX_CATEGORY_LENGTH,
        allow_blank=True,
        allow_lifestore_product_url=False,
    )
    if error:
        return error

    min_price, max_price, error = validate_price_range(min_price, max_price)
    if error:
        return error

    sort, error = normalize_sort_value(sort)
    if error:
        return error

    limit = bounded_int(limit, default=10, min_value=1, max_value=24)
    cursor = bounded_int(cursor, default=0, min_value=0, max_value=5000)
    currency = "LKR"

    products = [normalize_product(product) for product in load_products()]
    requested_family = _family_requested_by_query(q, category)

    if not products:
        return {
            "status": "no_local_products_file",
            "message": "No local LifeStore product JSON file was found.",
            "expected_path": str(PRODUCTS_JSON_PATH),
            "products": [],
        }

    ranked = []

    for product in products:
        if category:
            product_category = safe_text(product.get("category")).lower()
            if category.lower().strip() not in product_category:
                continue

        if requested_family:
            family_rule = PRODUCT_FAMILY_RULES.get(requested_family, {})
            if family_rule and not product_belongs_to_family(product, family_rule):
                continue

        price_value = product.get("price_value")
        if isinstance(price_value, (int, float)):
            if min_price is not None and price_value < float(min_price):
                continue
            if max_price is not None and price_value > float(max_price):
                continue

        if in_stock_only and not product_matches_availability(product, "in_stock"):
            continue

        score = keyword_score(product, q)
        if q.strip() == "" or score > 0:
            ranked.append((score, product))

    sort = sort.lower().strip()

    if sort in {"price_asc", "price-low-high", "low_to_high"}:
        ranked.sort(key=lambda item: item[1].get("price_value") or 0)
    elif sort in {"price_desc", "price-high-low", "high_to_low"}:
        ranked.sort(key=lambda item: item[1].get("price_value") or 0, reverse=True)
    elif sort in {"name_asc", "name"}:
        ranked.sort(key=lambda item: safe_text(item[1].get("name")).lower())
    else:
        ranked.sort(key=lambda item: item[0], reverse=True)

    start = max(int(cursor), 0)
    end = start + max(int(limit), 1)
    page_items = ranked[start:end]

    returned_products = []
    for _, product in page_items:
        image_result = _resolve_product_image(
            product_url=safe_text(product.get("url")),
            current_image_url=safe_text(product.get("image_url")),
        )
        product.update(image_result)

        if _bad_image_url(product.get("image_url")):
            product["image_url"] = ""
            product["image_available"] = False
            product["image_source"] = "not_available"
            product["image_note"] = "No valid product image found."

        returned_products.append(product)

    next_cursor = end if end < len(ranked) else None

    return {
        "status": "success",
        "q": q,
        "category": category,
        "min_price": min_price,
        "max_price": max_price,
        "in_stock_only": in_stock_only,
        "sort": sort,
        "currency": currency,
        "total_products_loaded": len(products),
        "matched_products": len(ranked),
        "cursor": cursor,
        "next_cursor": next_cursor,
        "returned_products": returned_products,
        "retrieval_policy": (
            query_family_filter_policy(q, category, default="keyword_product_search")
        ),
    }

@mcp.tool()
def lifestore_list_categories(depth: int = 1) -> dict[str, Any]:
    """
    List LifeStore product categories with product counts.

    The depth parameter is accepted for compatibility, but the local LifeStore JSON currently stores flat categories.
    """
    depth = bounded_int(depth, default=1, min_value=1, max_value=3)

    products = [normalize_product(product) for product in load_products()]
    category_counts: dict[str, int] = {}

    for product in products:
        category = safe_text(product.get("category")) or "Uncategorized"
        category_counts[category] = category_counts.get(category, 0) + 1

    categories = [
        {
            "name": name,
            "product_count": count,
            "browse_hint": f'Use category="{name}" in lifestore_search_products',
        }
        for name, count in sorted(category_counts.items(), key=lambda item: item[0].lower())
    ]

    return {
        "status": "success",
        "depth": depth,
        "total_categories": len(categories),
        "categories": categories,
    }


@mcp.tool()
def lifestore_strict_category_products(
    category: str,
    in_stock_only: bool = False,
    limit: int = 24,
    currency: str = "LKR",
) -> dict[str, Any]:
    """
    Return products that belong to an exact LifeStore category/offer label.

    Use this for:
    - Special Offers
    - New Connection Offers
    - Bundle Offers
    - Smart Home Corner
    - exact category browsing

    This tool does NOT use Qdrant semantic retrieval, because offer/category
    questions must be exact and should not include semantically similar products.
    """
    parsed_category = parse_json_string_if_needed(category)
    if isinstance(parsed_category, dict):
        category = str(parsed_category.get("category", parsed_category.get("query", category)))
        in_stock_only = bool(parsed_category.get("in_stock_only", in_stock_only))
        limit = parsed_category.get("limit", limit)
        currency = str(parsed_category.get("currency", currency))

    category, error = validate_text_input(
        category,
        "category",
        max_length=MAX_CATEGORY_LENGTH,
        allow_blank=False,
        allow_lifestore_product_url=False,
    )
    if error:
        return error

    limit = bounded_int(limit, default=24, min_value=1, max_value=50)
    currency = "LKR"

    canonical = canonical_category_query(category)
    products = [normalize_product(product) for product in load_products()]

    matched: list[dict[str, Any]] = []

    for product in products:
        if not product_has_exact_category(product, canonical):
            continue

        if in_stock_only and not product_matches_availability(product, "in_stock"):
            continue

        image_result = _resolve_product_image(
            product_url=safe_text(product.get("url")),
            current_image_url=safe_text(product.get("image_url")),
        )
        product.update(image_result)

        if _bad_image_url(product.get("image_url")):
            product["image_url"] = ""
            product["image_available"] = False
            product["image_source"] = "not_available"
            product["image_note"] = "No valid product image found."

        product["category_values"] = product_category_values(product)
        matched.append(product)

    matched.sort(key=lambda item: safe_text(item.get("name")).lower())

    bounded_limit = limit

    return {
        "status": "success",
        "category": canonical,
        "in_stock_only": in_stock_only,
        "currency": currency,
        "total_products_loaded": len(products),
        "matched_products": len(matched),
        "returned_products": matched[:bounded_limit],
        "retrieval_policy": "strict_category_only_no_qdrant",
    }


# ============================================================
# LifeStore Hybrid MCP Tools: Neo4j + Qdrant + image resolver
# Paste this block into your mcp_lifestore.py file
# after make_json_safe(...) and before @mcp.resource(...)
# ============================================================

import re
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from qdrant_client import QdrantClient
    from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
except Exception:
    QdrantClient = None
    QdrantVectorStore = None
    FastEmbedSparse = None
    RetrievalMode = None

try:
    # Prefer your existing project embedding setup when this MCP is running
    # inside the Ask SLT backend project.
    from core.llm import get_embedding_model as project_get_embedding_model # type: ignore
except Exception:
    project_get_embedding_model = None

try:
    # Fallback for standalone MCP runs.
    from langchain_openai import OpenAIEmbeddings
except Exception:
    OpenAIEmbeddings = None


QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip() or None
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large").strip()

LIFESTORE_QDRANT_DENSE_VECTOR_NAME = os.getenv("LIFESTORE_QDRANT_DENSE_VECTOR_NAME", "dense").strip()
LIFESTORE_QDRANT_SPARSE_VECTOR_NAME = os.getenv("LIFESTORE_QDRANT_SPARSE_VECTOR_NAME", "sparse").strip()

IMAGE_CACHE_PATH = Path(__file__).parent / "cache" / "lifestore_image_cache.json"

# Optional verified image lookup file generated by your product-image scraper.
# Supported formats:
# - JSON list: [{"url": "...", "image_url": "..."}, ...]
# - JSON dict: {"products": [{"url": "...", "image_url": "..."}, ...]}
# - JSON direct map: {"https://product-url": "https://image-url"}
# - CSV with columns like url/product_url/link and image_url/image/thumbnail
IMAGE_LOOKUP_JSON_ENV = os.getenv(
    "LIFESTORE_IMAGE_LOOKUP_JSON",
    PRODUCTS_JSON_ENV,
).strip()
IMAGE_LOOKUP_JSON_PATH = PROJECT_ROOT / IMAGE_LOOKUP_JSON_ENV

BAD_IMAGE_MARKERS = {
    "/themes/shop/images/",
    "chat-bot",
    "chat_bot",
    "chatbot",
    "union-pay",
    "visa",
    "master",
    "mastercard",
    "american",
    "payment",
    "payhere",
    "logo",
    "sltmobitel",
    "slt-mobitel",
    "footer",
    "header",
    "banner",
    "sprite",
    "loader",
    "ajax-loader",
    "placeholder",
    "default-image",
    "default_image",
    "no-image",
    "no_image",
    "noimage",
    "icon",
}


def _bad_image_url(url: Any) -> bool:
    """
    Return True when the URL is not a real product image.

    This guards both the MCP response and the frontend product cards from
    showing LifeStore payment logos, chatbot icons, header/footer images,
    and other theme assets.
    """
    value = safe_text(url).lower()

    if not value:
        return True

    if value.startswith("data:"):
        return True

    if value.endswith(".svg"):
        return True

    if not value.startswith(("http://", "https://", "//")):
        return True

    return any(marker in value for marker in BAD_IMAGE_MARKERS)


def _score_image_candidate(candidate_url: str, product_url: str = "") -> int:
    """
    Score product-page image candidates.

    Highest priority:
    - Drupal product image styles under /sites/default/files/styles/product...
    - Any /sites/default/files/ image
    Lowest priority:
    - theme/static images, logos, chatbot images, payment images
    """
    url_lower = safe_text(candidate_url).lower()
    product_url_lower = safe_text(product_url).lower()

    if _bad_image_url(url_lower):
        return -10000

    score = 0

    if "/sites/default/files/styles/product" in url_lower:
        score += 500

    if "/sites/default/files/styles/" in url_lower:
        score += 350

    if "/sites/default/files/" in url_lower:
        score += 300

    if "/public/" in url_lower:
        score += 80

    if "product" in url_lower:
        score += 60

    if any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        score += 30

    if "small" in url_lower:
        score += 10

    if "thumbnail" in url_lower:
        score += 8

    # If the product slug tokens appear in the image URL, prefer it.
    slug = product_url_lower.rstrip("/").split("/")[-1]
    for token in re.split(r"[-_/]+", slug):
        if token and len(token) >= 3 and token in url_lower:
            score += 12

    return score


def _load_image_cache() -> dict[str, str]:
    try:
        if IMAGE_CACHE_PATH.exists():
            return json.loads(IMAGE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_image_cache(cache: dict[str, str]) -> None:
    try:
        IMAGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        IMAGE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _normalize_image_lookup_key(product_url: Any) -> str:
    """
    Normalize LifeStore product URLs so exact matches work even when one source
    has a trailing slash and another does not.
    """
    value = safe_text(product_url)
    if not value:
        return ""

    # Keep query strings out of product URL matching.
    value = value.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return value


def _add_verified_lookup_pair(
    lookup: dict[str, str],
    product_url: Any,
    image_url: Any,
) -> None:
    product_key = _normalize_image_lookup_key(product_url)
    image_value = safe_text(image_url)

    if not product_key or not image_value:
        return

    if _bad_image_url(image_value):
        return

    lookup[product_key] = image_value


def _row_product_url(row: dict[str, Any]) -> str:
    return safe_text(
        row.get("url")
        or row.get("product_url")
        or row.get("productUrl")
        or row.get("link")
        or row.get("source_url")
        or row.get("source")
    )


def _row_image_url(row: dict[str, Any]) -> str:
    return safe_text(
        row.get("image_url")
        or row.get("imageUrl")
        or row.get("image")
        or row.get("thumbnail")
        or row.get("thumbnail_url")
        or row.get("photo")
    )


def _load_verified_image_lookup() -> dict[str, str]:
    """
    Load verified product image URLs from the product-image scraper output.

    This is the preferred image source when Neo4j/local JSON still contains old
    fallback images like union-pay.jpg or chat-bot.png.

    Supported formats:
    1. JSON list:
       [{"url": "...", "image_url": "..."}, ...]

    2. JSON object containing rows:
       {"products": [{"url": "...", "image_url": "..."}]}
       {"items": [{"product_url": "...", "image_url": "..."}]}
       {"results": [{"link": "...", "image": "..."}]}
       {"data": [{"url": "...", "thumbnail": "..."}]}

    3. JSON direct map:
       {"https://lifestore.lk/product/...": "https://lifestore.lk/sites/default/files/..."}

    4. CSV:
       Columns can be url/product_url/link and image_url/image/thumbnail.
    """
    lookup: dict[str, str] = {}

    if not IMAGE_LOOKUP_JSON_PATH.exists():
        return lookup

    suffix = IMAGE_LOOKUP_JSON_PATH.suffix.lower()

    try:
        if suffix == ".csv":
            with IMAGE_LOOKUP_JSON_PATH.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    if not isinstance(row, dict):
                        continue
                    _add_verified_lookup_pair(
                        lookup,
                        _row_product_url(row),
                        _row_image_url(row),
                    )
            return lookup

        data = json.loads(IMAGE_LOOKUP_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return lookup

    rows: Any = []

    if isinstance(data, dict):
        # Direct URL -> image map.
        if all(isinstance(value, str) for value in data.values()):
            for product_url, image_url in data.items():
                _add_verified_lookup_pair(lookup, product_url, image_url)
            return lookup

        rows = (
            data.get("products")
            or data.get("items")
            or data.get("results")
            or data.get("data")
            or []
        )

    elif isinstance(data, list):
        rows = data

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue

            _add_verified_lookup_pair(
                lookup,
                _row_product_url(row),
                _row_image_url(row),
            )

    return lookup


def _lookup_verified_product_image(product_url: str) -> str:
    lookup = _load_verified_image_lookup()
    product_key = _normalize_image_lookup_key(product_url)

    if not product_key:
        return ""

    image_url = safe_text(lookup.get(product_key))
    if image_url and not _bad_image_url(image_url):
        return image_url

    return ""


def _extract_image_candidates_from_html(product_url: str, html: str) -> list[str]:
    """
    Extract and rank real product image candidates from a LifeStore product page.

    The previous version accepted /themes/shop/images/chat-bot.png because it
    was a valid image URL. This version rejects theme/payment/chatbot images
    first, then ranks only product-looking image URLs.
    """
    if BeautifulSoup is None:
        return []

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    def add_candidate(value: Any) -> None:
        raw = safe_text(value)
        if not raw:
            return

        # srcset values may contain "url 1x, url 2x"; keep each URL.
        if "," in raw and (" " in raw or "http" in raw):
            for part in raw.split(","):
                src = part.strip().split(" ")[0]
                if src:
                    candidates.append(urljoin(product_url, src))
            return

        candidates.append(urljoin(product_url, raw))

    # High confidence metadata first.
    meta_selectors = [
        ("meta[property='og:image']", "content"),
        ("meta[property='og:image:url']", "content"),
        ("meta[property='og:image:secure_url']", "content"),
        ("meta[name='twitter:image']", "content"),
        ("meta[name='twitter:image:src']", "content"),
        ("link[rel='image_src']", "href"),
    ]

    for selector, attr in meta_selectors:
        for node in soup.select(selector):
            add_candidate(node.get(attr))

    # Drupal/LifeStore product-gallery image candidates.
    image_selectors = [
        ".product img",
        ".product-image img",
        ".product-images img",
        ".product-gallery img",
        ".field--name-field-image img",
        ".field--type-image img",
        ".commerce-product img",
        ".views-field-field-image img",
        ".node--type-product img",
        "article img",
        "main img",
        "img",
    ]

    image_attrs = [
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-zoom-image",
        "data-large-image",
        "src",
        "srcset",
    ]

    for selector in image_selectors:
        for img in soup.select(selector):
            classes = " ".join(img.get("class", []))
            alt = safe_text(img.get("alt"))
            title = safe_text(img.get("title"))

            for attr in image_attrs:
                add_candidate(img.get(attr))

            # Small boost by duplicating product-looking images.
            descriptor = f"{classes} {alt} {title}".lower()
            if any(word in descriptor for word in ["product", "image", "photo", "gallery"]):
                for attr in image_attrs:
                    add_candidate(img.get(attr))

    ranked: list[tuple[int, str]] = []
    seen = set()

    for candidate in candidates:
        candidate = safe_text(candidate)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        lower = candidate.lower()

        if not lower.startswith(("http://", "https://")):
            continue

        if _bad_image_url(lower):
            continue

        # Keep typical images and Drupal style image URLs.
        if not any(marker in lower for marker in [".jpg", ".jpeg", ".png", ".webp", "/styles/"]):
            continue

        score = _score_image_candidate(candidate, product_url=product_url)
        if score <= 0:
            continue

        ranked.append((score, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)

    return [candidate for _, candidate in ranked]


def _resolve_product_image(product_url: str, current_image_url: str = "") -> dict[str, Any]:
    """
    Return a frontend-safe product image.

    Resolution priority:
    1. Existing graph/local image, but only if it is a real product image.
    2. Local cache, but only if it is still valid.
    3. Verified product-image lookup file generated by your scraper.
    4. Product-page scraping as a final fallback.

    It never returns LifeStore theme/payment/chatbot assets to the frontend.
    """
    product_url = safe_text(product_url)
    current_image_url = safe_text(current_image_url)

    if current_image_url and not _bad_image_url(current_image_url):
        return {
            "image_url": current_image_url,
            "image_available": True,
            "image_source": "graph",
            "image_note": "Existing graph image was accepted.",
        }

    if not product_url:
        return {
            "image_url": "",
            "image_available": False,
            "image_source": "missing_product_url",
            "image_note": "No product URL was available for image lookup.",
        }

    cache = _load_image_cache()
    product_key = _normalize_image_lookup_key(product_url)
    cached = safe_text(cache.get(product_key) or cache.get(product_url))

    if cached and not _bad_image_url(cached):
        return {
            "image_url": cached,
            "image_available": True,
            "image_source": "cache",
            "image_note": "Resolved from local image cache.",
        }

    # Clear old bad cache values such as chat-bot.png or union-pay.jpg.
    if cached and _bad_image_url(cached):
        cache.pop(product_key, None)
        cache.pop(product_url, None)
        _save_image_cache(cache)

    # Preferred fallback: use your verified scraper output.
    verified_image = _lookup_verified_product_image(product_url)
    if verified_image and not _bad_image_url(verified_image):
        cache[product_key] = verified_image
        _save_image_cache(cache)

        return {
            "image_url": verified_image,
            "image_available": True,
            "image_source": "verified_image_lookup",
            "image_note": "Resolved from verified LifeStore image lookup file.",
            "image_lookup_path": str(IMAGE_LOOKUP_JSON_PATH),
        }

    # Last fallback: scrape the product page.
    if BeautifulSoup is None:
        return {
            "image_url": "",
            "image_available": False,
            "image_source": "bs4_missing",
            "image_note": "Install beautifulsoup4 to enable product-page image extraction.",
        }

    try:
        response = httpx.get(
            product_url,
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 AppleWebKit/537.36 "
                    "AskLifeStoreMCP/1.0 product image resolver"
                )
            },
        )
        response.raise_for_status()

        candidates = _extract_image_candidates_from_html(product_url, response.text)

        # Absolute final guard: do not allow theme/payment/chatbot images even
        # if they accidentally passed extraction.
        candidates = [candidate for candidate in candidates if not _bad_image_url(candidate)]

        if candidates:
            best = candidates[0]

            if _bad_image_url(best):
                return {
                    "image_url": "",
                    "image_available": False,
                    "image_source": "bad_candidate_rejected",
                    "image_note": "The best scraped candidate was a non-product site asset.",
                    "candidate_count": len(candidates),
                }

            cache[product_key] = best
            _save_image_cache(cache)

            return {
                "image_url": best,
                "image_available": True,
                "image_source": "product_page_scrape",
                "image_note": "Resolved by scraping the product page.",
                "candidate_count": len(candidates),
            }

    except Exception as error:
        return {
            "image_url": "",
            "image_available": False,
            "image_source": "product_page_scrape_failed",
            "image_note": str(error),
        }

    return {
        "image_url": "",
        "image_available": False,
        "image_source": "not_found",
        "image_note": (
            "No suitable product image candidate was found. "
            "Check LIFESTORE_IMAGE_LOOKUP_JSON and confirm it points to the verified image file."
        ),
        "image_lookup_path": str(IMAGE_LOOKUP_JSON_PATH),
        "image_lookup_exists": IMAGE_LOOKUP_JSON_PATH.exists(),
    }


def _extract_key_details(product: dict[str, Any], max_items: int = 6) -> list[str]:
    """
    Create compact feature bullets for answer cards.
    This is deterministic, so the frontend gets stable card data.
    """
    details: list[str] = []

    description = safe_text(product.get("description"))
    if description:
        description = re.sub(r"<[^>]+>", " ", description)
        description = re.sub(r"\s+", " ", description).strip()
        # Prefer customer-facing feature sentences before low-level specs.
        rough_sentences = re.split(r"(?<=[.!?])\s+|[•\u2022]|\s+-\s+|\n+", description)
        for sentence in rough_sentences:
            sentence = " ".join(sentence.split())
            if 20 <= len(sentence) <= 180:
                details.append(sentence)
            if len(details) >= max_items:
                break

    specs = product.get("specs") or product.get("specs_json") or {}
    if isinstance(specs, str):
        try:
            specs = json.loads(specs)
        except Exception:
            specs = {}

    if len(details) < max_items and isinstance(specs, dict):
        for key, value in specs.items():
            key_text = safe_text(key).replace("_", " ").strip().title()
            value_text = safe_text(value)
            if key_text and value_text:
                details.append(f"{key_text}: {value_text}")
            if len(details) >= max_items:
                break

    # De-duplicate while preserving order.
    unique = []
    seen = set()
    for item in details:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique[:max_items]


def _get_mcp_embedding_model():
    if project_get_embedding_model is not None:
        return project_get_embedding_model()

    if OpenAIEmbeddings is None:
        raise RuntimeError(
            "No embedding model is available. Either run MCP inside the backend project "
            "where core.llm.get_embedding_model exists, or install langchain-openai."
        )

    return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)


def _qdrant_search_documents(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """
    Search Qdrant from MCP.

    The result is returned as structured docs instead of one plain string so
    we can merge it with Neo4j product facts and frontend product cards.
    """
    if QdrantClient is None or QdrantVectorStore is None:
        return []

    try:
        client_kwargs = {"url": QDRANT_URL}
        if QDRANT_API_KEY:
            client_kwargs["api_key"] = QDRANT_API_KEY

        client = QdrantClient(**client_kwargs)

        try:
            if not client.collection_exists(LIFESTORE_QDRANT_COLLECTION):
                return []
        except Exception:
            # Some remote Qdrant setups may block collection_exists;
            # continue and let the search call decide.
            pass

        embeddings = _get_mcp_embedding_model()

        if FastEmbedSparse is not None and RetrievalMode is not None:
            vector_store = QdrantVectorStore(
                client=client,
                collection_name=LIFESTORE_QDRANT_COLLECTION,
                embedding=embeddings,
                sparse_embedding=FastEmbedSparse(model_name="Qdrant/bm25"),
                retrieval_mode=RetrievalMode.HYBRID,
                vector_name=LIFESTORE_QDRANT_DENSE_VECTOR_NAME,
                sparse_vector_name=LIFESTORE_QDRANT_SPARSE_VECTOR_NAME,
            )
        else:
            vector_store = QdrantVectorStore(
                client=client,
                collection_name=LIFESTORE_QDRANT_COLLECTION,
                embedding=embeddings,
                vector_name=LIFESTORE_QDRANT_DENSE_VECTOR_NAME,
            )

        docs = vector_store.similarity_search(query=query, k=max(int(limit), 1))
        results = []

        for index, doc in enumerate(docs):
            metadata = doc.metadata or {}
            results.append(
                {
                    "rank": index + 1,
                    "text": safe_text(doc.page_content),
                    "metadata": make_json_safe(metadata),
                    "source": safe_text(
                        metadata.get("source")
                        or metadata.get("source_url")
                        or metadata.get("url")
                    ),
                    "link": safe_text(
                        metadata.get("link")
                        or metadata.get("source_url")
                        or metadata.get("url")
                    ),
                    "title": safe_text(metadata.get("title")),
                }
            )

        return results

    except Exception:
        # Keep MCP robust. Neo4j/local product data can still answer.
        return []


def _hybrid_clean_terms(query: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9\-\/\s]+", " ", safe_text(query).lower())

    stop_words = {
        "is", "in", "the", "stock", "available", "availability", "do", "you",
        "have", "give", "me", "list", "all", "products", "product", "with",
        "price", "brand", "category", "url", "life", "lifestore", "of", "a",
        "an", "show", "tell", "about", "details", "detail", "what", "who",
        "seller", "sold", "by", "please", "and", "or", "for", "to",
    }

    terms = []

    for token in cleaned.split():
        token = token.strip()

        if len(token) < 2:
            continue

        if token in stop_words:
            continue

        terms.append(token)

    return list(dict.fromkeys(terms))

def _graph_product_search_rows(query: str, limit: int = 8) -> list[dict[str, Any]]:
    driver = neo4j_driver()
    if driver is None:
        return []

    terms = _hybrid_clean_terms(query)
    query_lower = safe_text(query).lower()
    wants_out_of_stock = (
        "out of stock" in query_lower
        or "out-of-stock" in query_lower
        or "sold out" in query_lower
    )
    wants_in_stock = (
        "in stock" in query_lower
    )

    cypher = """
    MATCH (p:Product)
    OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
    OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
    OPTIONAL MATCH (p)-[:HAS_AVAILABILITY]->(a:Availability)

    WITH p, b, c, a,
         toLower(coalesce(toString(p.name), "")) AS name_l,
         toLower(coalesce(toString(p.description), "")) AS desc_l,
         toLower(coalesce(toString(p.product_type), "")) AS type_l,
         toLower(coalesce(toString(p.seller), "")) AS seller_l,
         toLower(coalesce(toString(p.stock_status), "")) AS stock_l,
         toLower(coalesce(toString(b.name), "")) AS brand_l,
         toLower(coalesce(toString(c.name), "")) AS category_l,
         [tag IN coalesce(p.tags, []) | toLower(toString(tag))] AS tags_l

    WITH p, b, c, a,
         reduce(score = 0, term IN $terms |
            score
            + CASE WHEN name_l = term THEN 40 ELSE 0 END
            + CASE WHEN name_l CONTAINS term THEN 12 ELSE 0 END
            + CASE WHEN brand_l CONTAINS term THEN 5 ELSE 0 END
            + CASE WHEN category_l CONTAINS term THEN 4 ELSE 0 END
            + CASE WHEN type_l CONTAINS term THEN 4 ELSE 0 END
            + CASE WHEN seller_l CONTAINS term THEN 3 ELSE 0 END
            + CASE WHEN desc_l CONTAINS term THEN 2 ELSE 0 END
            + CASE WHEN any(tag IN tags_l WHERE tag CONTAINS term) THEN 2 ELSE 0 END
         ) AS score

    WHERE
        (size($terms) = 0 OR score > 0)
        AND ($wants_out_of_stock = false OR coalesce(p.stock_status, a.status, "") = "out_of_stock")
        AND ($wants_in_stock = false OR coalesce(p.stock_status, a.status, "") = "in_stock")

    RETURN
        coalesce(p.product_id, "") AS product_id,
        p.name AS name,
        p.seller AS seller,
        b.name AS brand,
        c.name AS category,
        p.product_type AS product_type,
        p.price AS price,
        p.price_value AS price_value,
        coalesce(p.stock_status, a.status, "") AS stock_status,
        p.stock AS stock,
        p.url AS url,
        p.image_url AS image_url,
        p.tags AS tags,
        NULL AS specs,
        p.specs_json AS specs_json,
        p.description AS description,
        score AS graph_score
    ORDER BY score DESC, name ASC
    LIMIT $limit
    """

    try:
        requested_limit = max(int(limit), 1)
        cypher_limit = requested_limit
        if _family_requested_by_query(query):
            # Pull a larger candidate pool because strict filtering may remove
            # accessories/support items that scored highly due to category/description.
            cypher_limit = max(requested_limit * 4, 24)

        with driver.session() as session:
            rows = list(
                session.run(
                    cypher,
                    terms=terms,
                    wants_out_of_stock=wants_out_of_stock,
                    wants_in_stock=wants_in_stock,
                    limit=cypher_limit,
                )
            )

        output = [make_json_safe(dict(row)) for row in rows]
        output = filter_products_for_query_family(output, query=query)
        return output[:requested_limit]

    except Exception:
        return []

    finally:
        driver.close()


def _local_product_search_rows(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """
    Fallback when Neo4j is unavailable. Uses the local JSON file.
    """
    products = [normalize_product(product) for product in load_products()]
    ranked = []
    requested_family = _family_requested_by_query(query)

    for product in products:
        if requested_family:
            family_rule = PRODUCT_FAMILY_RULES.get(requested_family, {})
            if family_rule and not product_belongs_to_family(product, family_rule):
                continue

        score = keyword_score(product, query)
        if score > 0:
            raw = product.get("raw") or {}
            row = {
                "product_id": product.get("product_id"),
                "name": product.get("name"),
                "seller": product.get("seller"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "product_type": product.get("product_type"),
                "price": product.get("price"),
                "price_value": product.get("price_value"),
                "stock_status": product.get("availability"),
                "stock": product.get("stock"),
                "url": product.get("url"),
                "image_url": product.get("image_url"),
                "tags": product.get("tags"),
                "specs": raw.get("specs", {}),
                "specs_json": raw.get("specs_json", {}),
                "description": product.get("description"),
                "graph_score": score,
            }
            ranked.append((score, row))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked[: max(int(limit), 1)]]


def _merge_graph_and_vector_results(
    graph_rows: list[dict[str, Any]],
    vector_docs: list[dict[str, Any]],
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Neo4j/local rows are the product source of truth.
    Qdrant docs add semantic context/excerpts.
    """
    products_by_key: dict[str, dict[str, Any]] = {}

    for row in graph_rows:
        url = safe_text(row.get("url"))
        product_id = safe_text(row.get("product_id"))
        name = safe_text(row.get("name") or row.get("product"))

        key = url or product_id or name.lower()
        if not key:
            continue

        specs = row.get("specs") or row.get("specs_json") or {}
        if isinstance(specs, str):
            try:
                specs = json.loads(specs)
            except Exception:
                specs = {}

        product = {
            "product_id": product_id,
            "name": name,
            "seller": safe_text(row.get("seller")),
            "brand": safe_text(row.get("brand")),
            "category": safe_text(row.get("category")),
            "product_type": safe_text(row.get("product_type")),
            "price": safe_text(row.get("price")),
            "price_value": row.get("price_value"),
            "currency": "LKR",
            "stock_status": safe_text(row.get("stock_status")),
            "stock": row.get("stock"),
            "url": url,
            "image_url": safe_text(row.get("image_url")),
            "tags": row.get("tags") or [],
            "specs": specs,
            "description": safe_text(row.get("description")),
            "key_details": [],
            "vector_evidence": [],
            "scores": {
                "graph_score": row.get("graph_score", 0),
                "vector_hits": 0,
            },
        }

        image_result = _resolve_product_image(
            product_url=product["url"],
            current_image_url=product["image_url"],
        )
        product.update(image_result)

        # Final safety guard before the product is returned to the frontend.
        # This prevents the chat from rendering LifeStore chatbot/payment/theme assets.
        if _bad_image_url(product.get("image_url")):
            product["image_url"] = ""
            product["image_available"] = False
            product["image_source"] = "not_available"
            product["image_note"] = "No valid product image found."

        product["key_details"] = _extract_key_details(product)

        products_by_key[key] = product

    # Attach vector evidence to matching product URLs/names where possible.
    for doc in vector_docs:
        doc_text = safe_text(doc.get("text"))
        doc_link = safe_text(doc.get("link") or doc.get("source"))
        doc_title = safe_text(doc.get("title"))
        matched = False

        for key, product in products_by_key.items():
            product_url = safe_text(product.get("url"))
            product_name = safe_text(product.get("name"))

            if (
                (product_url and product_url == doc_link)
                or (product_name and product_name.lower() in doc_text.lower())
                or (product_name and product_name.lower() in doc_title.lower())
            ):
                product["scores"]["vector_hits"] += 1
                if doc_text:
                    product["vector_evidence"].append(doc_text[:500])
                matched = True
                break

        # If Qdrant found relevant text but graph did not match anything,
        # keep it as evidence only; avoid inventing product cards.
        if not matched:
            continue

    return list(products_by_key.values())[: max(int(limit), 1)]


def _format_product_status(value: Any) -> str:
    text = safe_text(value)
    return text.replace("_", " ") if text else "Unknown"


def _build_answer_summary(query: str, products: list[dict[str, Any]]) -> str:
    """
    Deterministic fallback answer used when the FastAPI OpenAI writer is unavailable.

    The FastAPI proxy normally writes the final response with the OpenAI model, but
    this fallback keeps the original LifeStore formatting clean instead of cramming
    fields into one paragraph.
    """
    if not products:
        return (
            "I could not find a matching LifeStore product from the current "
            "Neo4j/Qdrant knowledge base."
        )

    product = products[0]

    product_name = safe_text(product.get("name")) or "This LifeStore product"
    product_type = safe_text(product.get("product_type"))
    brand = safe_text(product.get("brand"))
    seller = safe_text(product.get("seller")) or "the listed seller"

    type_phrase = f" {product_type}" if product_type else " product"
    brand_phrase = f" from {brand}" if brand else ""

    lines = [
        f"The {product_name} is a{type_phrase}{brand_phrase} sold by {seller}.",
        "",
        f"Product: {product_name}",
        f"Brand: {brand or 'Unknown'}",
        f"Seller: {seller or 'Unknown'}",
        f"Price: {safe_text(product.get('price')) or 'Unknown'}",
        f"Stock status: {_format_product_status(product.get('stock_status') or product.get('availability'))}",
        f"Category: {safe_text(product.get('category')) or 'Unknown'}",
        f"Product type: {product_type or 'Unknown'}",
        "",
        "Key details",
    ]

    details = product.get("key_details") or []
    if isinstance(details, list) and details:
        for detail in details[:6]:
            lines.append(f"- {safe_text(detail)}")
    else:
        description = safe_text(product.get("description"))
        lines.append(f"- {description}" if description else "- No extra feature details are available in the current LifeStore KB.")

    return "\n".join(lines)

@mcp.tool()
def lifestore_hybrid_product_search(
    query: str,
    limit: int = 5,
    include_vector_evidence: bool = False,
    search_mode: str = "auto",
    product_query: str | None = None,
) -> dict[str, Any]:
    """
    Main LifeStore answer retrieval tool.

    Use this tool when generating final chat answers because it combines:
    - Neo4j graph facts for exact product fields
    - Qdrant hybrid retrieval for semantic descriptions/features
    - image resolution for frontend product cards

    Frontend rule:
    Render products[].image_url inside an <img> tag.
    Do not print the image URL as plain text in the chat bubble.
    """
    parsed_query = parse_json_string_if_needed(query)

    if isinstance(parsed_query, dict):
        query = str(parsed_query.get("query", parsed_query.get("q", query)))
        limit = parsed_query.get("limit", limit)
        include_vector_evidence = bool(
            parsed_query.get("include_vector_evidence", include_vector_evidence)
        )
        search_mode = str(parsed_query.get("search_mode", search_mode))
        product_query = parsed_query.get("product_query", product_query)
    else:
        query = safe_text(parsed_query)

    query, error = validate_text_input(
        query,
        "query",
        max_length=MAX_QUERY_LENGTH,
        allow_blank=False,
        allow_lifestore_product_url=False,
    )
    if error:
        return error

    product_query, error = validate_text_input(
        product_query,
        "product_query",
        max_length=MAX_PRODUCT_QUERY_LENGTH,
        allow_blank=True,
        allow_lifestore_product_url=True,
    )
    if error:
        return error

    search_mode, error = normalize_search_mode(search_mode)
    if error:
        return error

    limit = bounded_int(limit, default=5, min_value=1, max_value=8)

    # Important: for direct product or availability questions, the FastAPI proxy
    # passes a clean product_query extracted by the OpenAI planner. Searching the
    # full user sentence can accidentally match a whole category such as "router".
    retrieval_query = product_query or query

    if search_mode in {"single_product", "availability", "purchase", "exact"}:
        limit = min(limit, 1)

    requested_family = _family_requested_by_query(retrieval_query)

    graph_rows = _graph_product_search_rows(query=retrieval_query, limit=limit)

    if not graph_rows:
        graph_rows = _local_product_search_rows(query=retrieval_query, limit=limit)

    vector_docs = _qdrant_search_documents(query=retrieval_query, limit=max(limit * 2, 8))

    products = _merge_graph_and_vector_results(
        graph_rows=graph_rows,
        vector_docs=vector_docs,
        query=retrieval_query,
        limit=limit,
    )

    if not include_vector_evidence:
        for product in products:
            product.pop("vector_evidence", None)

    answer = _build_answer_summary(query=query, products=products)

    return {
        "status": "success" if products else "not_found",
        "query": query,
        "retrieval_query": retrieval_query,
        "search_mode": search_mode,
        "retrieval": {
            "neo4j_or_local_rows": len(graph_rows),
            "qdrant_docs": len(vector_docs),
            "returned_products": len(products),
            "qdrant_collection": LIFESTORE_QDRANT_COLLECTION,
            "retrieval_policy": (
                query_family_filter_policy(retrieval_query, default="hybrid_graph_vector_search")
            ),
        },
        "answer": answer,
        "products": products,
        "frontend_contract": {
            "render_as": "assistant_answer_with_product_cards",
            "answer_field": "answer",
            "cards_field": "products",
            "image_rule": "Render products[].image_url in an img tag. Never show raw image URLs as message text.",
        },
    }


@mcp.tool()
def lifestore_precise_product_lookup(
    product_query: str,
    include_vector_evidence: bool = True,
) -> dict[str, Any]:
    """
    Resolve one specific LifeStore product.

    Use this for:
    - "Tell me about TP-Link TD-W8961ND"
    - "Is TP-Link TD-W8961ND available?"
    - "I need to buy Archer AX20"

    This tool intentionally returns only one frontend-ready product card so the
    chat does not show a whole category for a single-product question.
    """
    product_query, error = validate_text_input(
        product_query,
        "product_query",
        max_length=MAX_PRODUCT_QUERY_LENGTH,
        allow_blank=False,
        allow_lifestore_product_url=True,
    )
    if error:
        return error

    return lifestore_hybrid_product_search(
        query=product_query,
        product_query=product_query,
        search_mode="single_product",
        limit=1,
        include_vector_evidence=include_vector_evidence,
    )


@mcp.tool()
def lifestore_availability_lookup(
    product_query: str,
    requested_availability: str = "in_stock",
) -> dict[str, Any]:
    """
    Check the availability of one specific LifeStore product and return only that product.

    This avoids the old behavior where asking "is A available?" could return every
    product in A's category.
    """
    product_query, error = validate_text_input(
        product_query,
        "product_query",
        max_length=MAX_PRODUCT_QUERY_LENGTH,
        allow_blank=False,
        allow_lifestore_product_url=True,
    )
    if error:
        return error

    requested_availability, error = normalize_requested_availability(requested_availability)
    if error:
        return error

    lookup = lifestore_precise_product_lookup(
        product_query=product_query,
        include_vector_evidence=False,
    )

    products = lookup.get("products") or []
    if not products:
        return {
            "status": "not_found",
            "message": "No matching LifeStore product was found.",
            "product_query": product_query,
            "requested_availability": requested_availability,
            "products": [],
        }

    product = products[0]
    actual = product.get("stock_status") or product.get("availability")
    matches = product_matches_availability(
        {"availability": actual, "stock": product.get("stock")},
        requested_availability,
    )

    return {
        "status": "success",
        "product_query": product_query,
        "requested_availability": requested_availability,
        "matches_requested_availability": matches,
        "actual_availability": actual,
        "product": product,
        "products": [product],
        "retrieval": lookup.get("retrieval") or {},
    }


@mcp.tool()
def lifestore_compare_products(
    query: str,
    product_queries: Any = None,
    limit: int = 4,
    include_vector_evidence: bool = True,
) -> dict[str, Any]:
    """
    Return a comparison-ready product set.

    If product_queries is provided, it should be a list of product names/IDs/URLs.
    If it is not provided, the tool searches the query and returns up to `limit`
    products for a category-style comparison.
    """
    query, error = validate_text_input(
        query,
        "query",
        max_length=MAX_QUERY_LENGTH,
        allow_blank=True,
        allow_lifestore_product_url=False,
    )
    if error:
        return error

    parsed_queries, error = validate_product_query_list(product_queries)
    if error:
        return error

    if not query and not parsed_queries:
        return invalid_input_response(
            "query",
            "provide either a comparison query or product_queries.",
        )

    limit = bounded_int(limit, default=4, min_value=2, max_value=6)

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    if parsed_queries:
        for item_query in parsed_queries:

            result = lifestore_precise_product_lookup(
                product_query=item_query,
                include_vector_evidence=include_vector_evidence,
            )

            for product in result.get("products") or []:
                key = safe_text(product.get("url") or product.get("product_id") or product.get("name"))
                if key and key not in seen:
                    seen.add(key)
                    collected.append(product)

            if len(collected) >= limit:
                break

    if len(collected) < 2:
        fallback = lifestore_hybrid_product_search(
            query=query,
            search_mode="comparison",
            limit=limit,
            include_vector_evidence=include_vector_evidence,
        )
        for product in fallback.get("products") or []:
            key = safe_text(product.get("url") or product.get("product_id") or product.get("name"))
            if key and key not in seen:
                seen.add(key)
                collected.append(product)

    collected = collected[:limit]
    answer = _build_answer_summary(query=query, products=collected)

    return {
        "status": "success" if collected else "not_found",
        "query": query,
        "product_queries": parsed_queries,
        "products": collected,
        "answer": answer,
        "retrieval": {
            "returned_products": len(collected),
            "mode": "comparison",
            "qdrant_collection": LIFESTORE_QDRANT_COLLECTION,
        },
        "frontend_contract": {
            "render_as": "comparison_image_grid",
            "display": "comparison",
            "answer_field": "answer",
            "cards_field": "products",
            "image_rule": "Render compared products side-by-side with images below the comparison answer.",
        },
    }



@mcp.resource("lifestore://source")
def lifestore_source() -> str:
    """
    LifeStore source information.
    """
    return json.dumps(
        {
            "source_name": "LifeStore all products page",
            "source_url": LIFESTORE_SOURCE_URL,
            "agent_name": LIFESTORE_AGENT_NAME,
            "qdrant_collection": LIFESTORE_QDRANT_COLLECTION,
        },
        indent=2,
    )


@mcp.resource("lifestore://architecture")
def lifestore_architecture() -> str:
    """
    Ask LifeStore architecture summary.
    """
    return """
Ask LifeStore MCP Architecture

Existing project:
- FastAPI backend
- Qdrant vector database
- Neo4j product graph
- LifeStore product ingestion
- KB refresh automation handled outside the public MCP tool set

MCP role:
- Expose safe customer-facing LifeStore product tools to MCP-compatible AI clients.
- Search local LifeStore product data.
- List LifeStore categories.
- Run exact category/offer product searches.
- Run lifestore_hybrid_product_search for Neo4j + Qdrant answers.
- Resolve frontend-ready product image_url fields through the internal image resolver.
- Return product cards with safe image URLs for frontend rendering.

Security:
- MCP is private inside the Docker network.
- MCP Streamable HTTP access is protected with Bearer-token authentication.
- Admin/demo/write tools are not exposed in the public LifeStore MCP server.
"""


@mcp.prompt()
def ask_lifestore_prompt(user_question: str) -> str:
    """
    Reusable prompt for LifeStore shopping assistant behavior.
    """
    return f"""
You are Ask LifeStore, an AI assistant for LifeStore products.

User question:
{user_question}

Use MCP tools when needed:
- Always call lifestore_hybrid_product_search first for normal user product queries and final answer generation.
- lifestore_hybrid_product_search combines Neo4j graph facts, Qdrant semantic retrieval, and product image resolution.
- lifestore_search_products is only a fallback/simple catalogue search tool.
- lifestore_list_categories is only for category browsing.
- lifestore_strict_category_products is only for exact category or offer-based browsing.
- lifestore_precise_product_lookup is only for one specific named product.
- lifestore_availability_lookup is only for direct stock or availability questions.
- lifestore_compare_products is only for comparing multiple products.

Answer based only on available LifeStore data.
Do not print raw image URLs in the chat answer. Put product image URLs only inside the structured products[].image_url field so the frontend can render them as images.
If the data is missing, say that the current LifeStore KB does not contain enough information.
"""


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http").strip().lower()
    host = os.getenv("MCP_HOST", "0.0.0.0").strip()
    port = int(os.getenv("MCP_PORT", "8001"))

    if transport in {"streamable-http", "streamable_http", "http"}:
        app = mcp.streamable_http_app()
        app.add_middleware(MCPBearerAuthMiddleware)
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=os.getenv("MCP_LOG_LEVEL", "info").lower(),
        )
    else:
        mcp.run(transport=transport)

