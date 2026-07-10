import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase


# ---------------------------------------------------------------------
# Paths + .env
# ---------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
BASE_URL = os.getenv("LIFESTORE_GRAPH_BASE_URL", "https://lifestore.lk").rstrip("/")

START_URLS = [
    url.strip()
    for url in os.getenv(
        "LIFESTORE_GRAPH_START_URLS",
        "https://lifestore.lk/products?categories=All&products,https://lifestore.lk/categories",
    ).split(",")
    if url.strip()
]

OUTPUT_FILE = ROOT_DIR / "backend/data/real/lifestore_all.json"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}

REQUEST_DELAY_SECONDS = float(os.getenv("LIFESTORE_GRAPH_DELAY", "0.5"))

# If true, deletes existing Product nodes and dangling Brand/Category/Availability nodes.
# Use true once when changing from old LS0001 IDs to stable URL-based IDs.
CLEAR_GRAPH = os.getenv("CLEAR_LIFESTORE_GRAPH", "false").lower() == "true"

# If true, products not found in the latest scrape are removed.
# Keep false unless you are sure you want exact sync behavior.
REMOVE_MISSING_PRODUCTS = (
    os.getenv("REMOVE_MISSING_LIFESTORE_PRODUCTS", "false").lower() == "true"
)

MAX_LISTING_PAGES = int(os.getenv("LIFESTORE_GRAPH_MAX_LISTING_PAGES", "500"))

# 0 means unlimited.
PRODUCT_URL_LIMIT = int(os.getenv("LIFESTORE_GRAPH_PRODUCT_URL_LIMIT", "0"))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") or "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE") or "neo4j"


# ---------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------
def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_url(url: str) -> str:
    clean_url, _ = urldefrag(url)
    return clean_url.strip().rstrip("/")


def parse_price_value(text: str) -> Optional[float]:
    match = re.search(r"Rs\.?\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)

    if not match:
        return None

    raw = match.group(1).replace(",", "")

    try:
        return float(raw)
    except ValueError:
        return None


def format_price(price: Optional[float]) -> str:
    if price is None:
        return ""

    return f"Rs. {price:,.2f}"


def fetch_page(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def get_lines(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text("\n")
    lines = [clean_text(line) for line in text.splitlines()]
    return [line for line in lines if line]


def make_stable_product_id(product_url: str) -> str:
    """
    Create a stable Product ID from the product URL.

    This is better than LS0001, LS0002, etc. because product ordering can change
    when LifeStore adds/removes products.
    """
    parsed = urlparse(product_url)
    slug = parsed.path.rstrip("/").split("/")[-1]

    clean_slug = re.sub(r"[^a-zA-Z0-9]+", "_", slug).strip("_").upper()
    digest = hashlib.sha1(product_url.encode("utf-8")).hexdigest()[:8].upper()

    if clean_slug:
        return f"LS_{clean_slug[:40]}_{digest}"

    return f"LS_{digest}"


# ---------------------------------------------------------------------
# Listing page crawler
# ---------------------------------------------------------------------
def is_same_site(url: str) -> bool:
    parsed_base = urlparse(BASE_URL)
    parsed_url = urlparse(url)

    return parsed_url.netloc in {
        parsed_base.netloc,
        "lifestore.lk",
        "www.lifestore.lk",
    }


def is_product_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"

    return is_same_site(url) and path.startswith("/product/")


def is_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"

    if not is_same_site(url):
        return False

    if path.startswith("/product/"):
        return False

    allowed_prefixes = [
        "/products",
        "/categories",
        "/category",
    ]

    return any(path.startswith(prefix) for prefix in allowed_prefixes)


def extract_product_urls(soup: BeautifulSoup, current_url: str) -> list[str]:
    urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()

        if not href:
            continue

        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        full_url = normalize_url(urljoin(current_url, href))

        if not is_product_url(full_url):
            continue

        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    return urls


def extract_listing_urls(soup: BeautifulSoup, current_url: str) -> list[str]:
    urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()

        if not href:
            continue

        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        full_url = normalize_url(urljoin(current_url, href))

        if not is_listing_url(full_url):
            continue

        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    return urls


def scrape_listing_pages() -> list[str]:
    """
    Flexible LifeStore listing crawler.

    Instead of depending only on /categories and text like 'Next page',
    this crawls useful LifeStore listing/category/product-list pages and
    collects every /product/ URL it can find.

    This makes the graph update more flexible if LifeStore adds:
    - more category pages
    - more pagination pages
    - more product listing URLs
    - products under /products?... links
    """
    queue: list[str] = []
    queued: set[str] = set()
    visited_pages: set[str] = set()

    all_product_urls: list[str] = []
    seen_products: set[str] = set()

    for start_url in START_URLS:
        full_start_url = normalize_url(start_url)

        if full_start_url not in queued:
            queue.append(full_start_url)
            queued.add(full_start_url)

    page_num = 1

    while queue and len(visited_pages) < MAX_LISTING_PAGES:
        url = queue.pop(0)

        if url in visited_pages:
            continue

        visited_pages.add(url)

        print(f"Scraping listing page {page_num}: {url}")

        try:
            html = fetch_page(url)
        except Exception as exc:
            print(f"  failed listing page: {url} -> {exc}")
            page_num += 1
            continue

        soup = BeautifulSoup(html, "html.parser")

        product_urls = extract_product_urls(soup, url)
        print(f"  found product urls: {len(product_urls)}")

        for product_url in product_urls:
            normalized_product_url = normalize_url(product_url)

            if normalized_product_url not in seen_products:
                seen_products.add(normalized_product_url)
                all_product_urls.append(normalized_product_url)

                if PRODUCT_URL_LIMIT > 0 and len(all_product_urls) >= PRODUCT_URL_LIMIT:
                    print(f"Product URL limit reached: {PRODUCT_URL_LIMIT}")
                    print(f"Visited listing pages: {len(visited_pages)}")
                    print(f"Total unique product URLs: {len(all_product_urls)}")
                    return all_product_urls

        listing_urls = extract_listing_urls(soup, url)

        for next_url in listing_urls:
            if next_url in visited_pages or next_url in queued:
                continue

            if len(visited_pages) + len(queue) >= MAX_LISTING_PAGES:
                break

            queue.append(next_url)
            queued.add(next_url)

        print(f"  queued listing pages: {len(queue)}")

        page_num += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Visited listing pages: {len(visited_pages)}")
    print(f"Total unique product URLs: {len(all_product_urls)}")

    return all_product_urls


# ---------------------------------------------------------------------
# Product page extraction logic
# ---------------------------------------------------------------------
def extract_name(lines: list[str], soup: BeautifulSoup) -> str:
    for i in range(len(lines) - 2):
        if lines[i] == "Home" and lines[i + 1] == "Products":
            return lines[i + 2]

    h1 = soup.find("h1")

    if h1:
        return clean_text(h1.get_text(" "))

    if soup.title:
        return clean_text(soup.title.get_text()).replace("| LifeStore", "").strip()

    return ""


def extract_category(lines: list[str], name: str) -> str:
    for i, line in enumerate(lines):
        if line == "Product Reviews":
            for j in range(i + 1, min(i + 6, len(lines))):
                candidate = lines[j]

                if candidate not in {"Home", "Products", name, "####", "* * *"}:
                    if "Rs." not in candidate:
                        return candidate

    return ""


def extract_price(lines: list[str], name: str) -> Optional[float]:
    if name not in lines:
        return None

    start_idx = lines.index(name)
    end_idx = len(lines)

    for marker in [
        "Quantity",
        "Overview",
        "Specification",
        "Product Description :",
        "Related Products",
    ]:
        for i in range(start_idx, len(lines)):
            if lines[i] == marker:
                end_idx = min(end_idx, i)
                break

    window = lines[start_idx:end_idx]
    price_candidates = []

    for line in window:
        if "Rs." in line or "Rs" in line:
            price = parse_price_value(line)

            if price is not None:
                price_candidates.append(price)

    if price_candidates:
        return price_candidates[-1]

    return None


def extract_stock(lines: list[str], name: str) -> int:
    """
    LifeStore-specific stock logic.

    If the product page has 'Quantity' near the product area, it means the
    product is purchasable/in stock. If not, it is treated as out of stock.
    """
    if name not in lines:
        return 0

    start_idx = lines.index(name)
    end_idx = len(lines)

    for marker in [
        "Product Description :",
        "Related Products",
        "Overview",
        "Specification",
    ]:
        for i in range(start_idx, len(lines)):
            if lines[i] == marker:
                end_idx = min(end_idx, i)
                break

    window = lines[start_idx:end_idx]

    return 1 if "Quantity" in window else 0


def extract_stock_status(stock: int) -> str:
    return "in_stock" if stock == 1 else "out_of_stock"


def extract_description(lines: list[str]) -> str:
    start = None
    end = None

    for i, line in enumerate(lines):
        if line == "Product Description :":
            start = i + 1
            break

    if start is None:
        return ""

    for i in range(start, len(lines)):
        if lines[i] == "Related Products":
            end = i
            break

    desc_lines = lines[start:end] if end is not None else lines[start:]
    cleaned = []

    for line in desc_lines:
        if line in {"Overview", "Specification", "Quantity", "-", "+"}:
            continue

        cleaned.append(line)

    return clean_text(" ".join(cleaned))


# ---------------------------------------------------------------------
# Product image extraction logic
# ---------------------------------------------------------------------
URL_RE = re.compile(r"https?://[^\s,\"'<>]+")


def absolute_url(url: str, current_url: str = BASE_URL) -> str:
    return normalize_url(urljoin(current_url, url))


def extract_urls(value: Any) -> list[str]:
    """
    Extract URLs from any accidentally-combined text.

    This is the same guard used in the standalone image scraper to prevent
    product_url and image_url from being tangled together.
    """
    text = str(value or "")
    urls = []

    direct = normalize_url(text)
    if direct.startswith(("http://", "https://")):
        urls.append(direct)

    for match in URL_RE.finditer(text):
        url = normalize_url(match.group(0)).rstrip(").;]")
        if url and url not in urls:
            urls.append(url)

    return urls


def is_product_page_url(url: str) -> bool:
    parsed = urlparse(url.lower())
    return "lifestore.lk" in parsed.netloc and "/product/" in parsed.path


def pick_product_url(*values: Any) -> str:
    """
    Pick only the LifeStore product page URL, never an image URL.
    """
    for value in values:
        for url in extract_urls(value):
            if is_product_page_url(url):
                return normalize_url(url).rstrip("/")

    return ""


def extract_srcset_url(srcset: str | None) -> str:
    if not srcset:
        return ""

    candidates = []

    for part in srcset.split(","):
        item = part.strip()
        if not item:
            continue

        url_part = item.split()[0].strip()
        if url_part:
            candidates.append(url_part)

    return candidates[-1] if candidates else ""


def extract_url_from_img_tag(img) -> str:
    for attr in [
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-url",
        "data-image",
    ]:
        value = img.get(attr)
        if value:
            return value

    for attr in ["srcset", "data-srcset"]:
        value = extract_srcset_url(img.get(attr))
        if value:
            return value

    return ""


def looks_like_image_url(url: str) -> bool:
    low = url.lower()
    parsed = urlparse(low)
    path = parsed.path

    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return True

    if "/sites/default/files/" in low and "/styles/" in low:
        return True

    return False


def is_bad_lifestore_image(url: str, context: str = "") -> bool:
    """
    Reject global/header/footer/payment/chat assets.

    This is the same bad-image filter used in the standalone image scraper.
    """
    value = f"{url} {context}".lower()

    bad_hints = [
        "970_90",
        "inline-images/970_90",
        "eteleshop",
        "teleshop",
        "/themes/shop/images/",
        "union-pay",
        "visa",
        "master",
        "american",
        "payment",
        "logo",
        "sltmobitel",
        "chat",
        "footer",
        "header",
        "banner",
        "sprite",
        "loader",
        "ajax-loader",
        "placeholder",
        "default-image",
        "no-image",
    ]

    if any(hint in value for hint in bad_hints):
        return True

    if value.startswith("data:"):
        return True

    if urlparse(url).path.lower().endswith(".svg"):
        return True

    return False


def pick_image_url(*values: Any) -> str:
    """
    Pick only a valid product image URL, never a product page URL.
    """
    for value in values:
        for url in extract_urls(value):
            full_url = absolute_url(url)
            if looks_like_image_url(full_url) and not is_bad_lifestore_image(full_url):
                return full_url

    return ""


def slug_tokens(text: str, min_len: int = 3) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-zA-Z0-9]+", (text or "").lower())
        if len(token) >= min_len
    ]


@dataclass
class ImageCandidate:
    image_url: str
    score: int
    reason: str
    alt: str = ""
    source_tag: str = ""


def score_image_candidate(
    image_url: str,
    product_name: str,
    product_url: str,
    context: str,
    base_score: int,
    reason: str,
    alt: str = "",
    source_tag: str = "",
) -> ImageCandidate | None:
    full_url = absolute_url(image_url, product_url)

    if not looks_like_image_url(full_url):
        return None

    if is_bad_lifestore_image(full_url, context):
        return None

    parsed_image_path = urlparse(full_url).path.lower()
    image_filename = parsed_image_path.split("/")[-1].lower()

    low_url = full_url.lower()
    low_context = clean_text(context).lower()
    low_alt = clean_text(alt).lower()
    low_product_name = clean_text(product_name).lower()

    candidate_blob = f"{image_filename} {low_alt} {low_context}"

    score = base_score

    if "/styles/product_image_small/" in low_url:
        score += 120

    if "/styles/product_block_images/" in low_url:
        score += 85

    if "/styles/product_image_large/" in low_url:
        score += 75

    if "/styles/" in low_url and "/sites/default/files/" in low_url:
        score += 60

    if "/sites/default/files/" in low_url:
        score += 35

    product_tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9]+", low_product_name)
        if len(token) >= 2 or token.isdigit()
    ]

    generic_tokens = {
        "smart",
        "bluetooth",
        "speaker",
        "display",
        "phone",
        "telephone",
        "camera",
        "color",
        "colour",
        "wireless",
        "wifi",
        "wi",
        "fi",
        "router",
        "switch",
        "adapter",
        "adaptor",
        "access",
        "point",
        "range",
        "extender",
        "outdoor",
        "indoor",
        "power",
        "backup",
        "guard",
        "the",
        "and",
        "with",
        "for",
    }

    distinctive_tokens = [
        token
        for token in product_tokens
        if token not in generic_tokens
    ]

    token_hits = 0
    distinctive_hits = 0

    for token in product_tokens:
        if token in image_filename:
            score += 18
            token_hits += 1

        if token in low_alt:
            score += 18
            token_hits += 1

        if token in low_context:
            score += 8
            token_hits += 1

    for token in distinctive_tokens:
        if token in candidate_blob:
            score += 35
            distinctive_hits += 1

    # Exact/near-exact alt match is very strong.
    if low_alt and low_alt == low_product_name:
        score += 220

    elif low_alt and low_product_name in low_alt:
        score += 150

    elif low_alt and low_alt in low_product_name:
        score += 90

    # Penalize another product's alt text.
    if low_alt and low_alt != low_product_name:
        alt_tokens = [
            token
            for token in re.split(r"[^a-zA-Z0-9]+", low_alt)
            if len(token) >= 2 or token.isdigit()
        ]

        product_token_set = set(product_tokens)
        alt_token_set = set(alt_tokens)

        overlap = len(product_token_set & alt_token_set)
        foreign_tokens = [
            token
            for token in alt_tokens
            if token not in product_token_set and token not in generic_tokens
        ]

        if foreign_tokens and overlap <= 1:
            score -= 220

        elif foreign_tokens and overlap <= 2:
            score -= 120

    # Main fix:
    # If the product has distinctive/model tokens but the candidate image does not
    # contain enough of them, it is probably a related product image.
    #
    # Examples fixed by this:
    # - Alexa Echo Dot / Show selecting TeDi Alexa image
    # - SonicGear Neox 7 selecting RDO 30x image
    # - Cudy 24-Port selecting 8-Port image
    useful_distinctive_tokens = [
        token
        for token in distinctive_tokens
        if len(token) >= 3 or any(ch.isdigit() for ch in token)
    ]

    if useful_distinctive_tokens:
        required_hits = 1

        if len(useful_distinctive_tokens) >= 3:
            required_hits = 2

        if distinctive_hits < required_hits:
            score -= 180

    related_hints = [
        "related products",
        "similar products",
        "you may also like",
        "view product",
        "add to cart",
    ]

    if any(hint in low_context for hint in related_hints):
        score -= 120

    if token_hits == 0:
        score -= 100

    if token_hits == 0 and "/styles/product_" not in low_url:
        score -= 80

    return ImageCandidate(
        image_url=full_url,
        score=score,
        reason=reason,
        alt=alt,
        source_tag=source_tag,
    )


def collect_image_candidates(
    soup: BeautifulSoup,
    product_name: str,
    product_url: str,
) -> list[ImageCandidate]:
    candidates: list[ImageCandidate] = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        context = clean_text(a.get_text(" "))

        img = a.find("img")
        alt = ""
        if img:
            alt = clean_text(img.get("alt") or img.get("title") or "")
            context = clean_text(f"{context} {alt}")

        candidate = score_image_candidate(
            href,
            product_name=product_name,
            product_url=product_url,
            context=context,
            base_score=70,
            reason="anchor_href_image",
            alt=alt,
            source_tag="a[href]",
        )

        if candidate:
            candidates.append(candidate)

    for img in soup.find_all("img"):
        raw_url = extract_url_from_img_tag(img)
        if not raw_url:
            continue

        alt = clean_text(img.get("alt") or img.get("title") or "")
        parent_text = clean_text(img.parent.get_text(" ")) if img.parent else ""
        context = clean_text(f"{alt} {parent_text}")

        candidate = score_image_candidate(
            raw_url,
            product_name=product_name,
            product_url=product_url,
            context=context,
            base_score=60,
            reason="img_tag",
            alt=alt,
            source_tag="img",
        )

        if candidate:
            candidates.append(candidate)

    for source in soup.find_all("source"):
        raw_url = extract_srcset_url(source.get("srcset") or source.get("data-srcset"))
        if not raw_url:
            continue

        parent_text = clean_text(source.parent.get_text(" ")) if source.parent else ""

        candidate = score_image_candidate(
            raw_url,
            product_name=product_name,
            product_url=product_url,
            context=parent_text,
            base_score=55,
            reason="source_srcset",
            source_tag="source",
        )

        if candidate:
            candidates.append(candidate)

    for selector in [
        {"property": "og:image"},
        {"name": "og:image"},
        {"property": "twitter:image"},
        {"name": "twitter:image"},
    ]:
        meta = soup.find("meta", selector)
        if not meta or not meta.get("content"):
            continue

        candidate = score_image_candidate(
            meta["content"],
            product_name=product_name,
            product_url=product_url,
            context="metadata image",
            base_score=5,
            reason=f"meta_{list(selector.values())[0]}",
            source_tag="meta",
        )

        if candidate:
            candidates.append(candidate)

    best_by_url: dict[str, ImageCandidate] = {}

    for candidate in candidates:
        existing = best_by_url.get(candidate.image_url)

        if existing is None or candidate.score > existing.score:
            best_by_url[candidate.image_url] = candidate

    return sorted(best_by_url.values(), key=lambda item: item.score, reverse=True)


def extract_best_product_image_from_soup(
    soup: BeautifulSoup,
    product_url: str,
    expected_name: str = "",
) -> dict[str, Any]:
    """
    Integrated version of the standalone image scraper logic.

    The graph builder already has the product page HTML, so this function uses
    the same scoring/filtering logic without fetching the product page again.
    """
    clean_product_url = pick_product_url(product_url) or normalize_url(product_url).rstrip("/")
    product_name = expected_name or extract_name([], soup)

    candidates = collect_image_candidates(soup, product_name, clean_product_url)

    if not candidates:
        return {
            "status": "image_not_found",
            "product_url": clean_product_url,
            "name": product_name,
            "image_url": "",
            "score": 0,
            "reason": "",
            "top_candidates": [],
        }

    best = candidates[0]

    return {
        "status": "success",
        "product_url": clean_product_url,
        "name": product_name,
        "image_url": best.image_url,
        "score": best.score,
        "reason": best.reason,
        "top_candidates": [asdict(item) for item in candidates[:5]],
    }


def extract_seller(lines: list[str]) -> str:
    for i, line in enumerate(lines):
        if line == "Sold by :" and i + 1 < len(lines):
            return lines[i + 1]

    return ""


# ---------------------------------------------------------------------
# Controlled enrichment
# ---------------------------------------------------------------------
def infer_brand(name: str, seller: str, category: str) -> str:
    """
    Controlled brand inference.
    Avoids creating bad brands like View, Lunch, Thermal, etc.
    """
    low_name = name.lower()
    low_seller = seller.lower()
    low_category = category.lower()

    if low_name.startswith("tp-link"):
        return "TP-Link"

    if low_name.startswith("prolink"):
        return "Prolink"

    if low_name.startswith("alcatel"):
        return "Alcatel"

    if low_name.startswith("alexa echo") or low_name.startswith("echo"):
        return "Amazon Alexa"

    if low_name.startswith("aj "):
        return "AJ"

    if low_name.startswith("cudy"):
        return "Cudy"

    if low_name.startswith("comstox"):
        return "Comstox"

    if low_name.startswith("d-link"):
        return "D-Link"

    if low_name.startswith("dcp-link"):
        return "DCP-LINK"

    if low_name.startswith("d-top") or low_name.startswith("dtop"):
        return "D-TOP"

    if low_name.startswith("e-mark") or low_name.startswith("emark"):
        return "EMARK"

    if low_name.startswith("siyol"):
        return "SIYOL"

    if low_name.startswith("sonicgear"):
        return "SonicGear"

    if low_name.startswith("tenda"):
        return "Tenda"

    if low_name.startswith("uniview"):
        return "UNIVIEW"

    if low_name.startswith("peotv") or low_name.startswith("peo tv"):
        return "PEOTV"

    if "makers" in low_seller or "maker's box" in low_name or "makers box" in low_name:
        return "Maker's Box"

    if "smart home" in low_category and "alexa" in low_name:
        return "Amazon Alexa"

    return "Unknown"


def infer_product_type(name: str, category: str, description: str) -> str:
    name_l = name.lower()
    category_l = category.lower()

    category_defaults = {
        "accessories": "accessory",
        "3g & 4g routers": "router",
        "wi-fi adaptors": "wifi adaptor",
        "access points & extenders": "wifi extender",
        "power backups": "power backup",
        "power guards": "power guard",
        "cordless telephones": "cordless phone",
        "telephones": "telephone",
        "cli/sms telephones": "caller id telephone",
        "smart home corner": "smart home device",
        "stem toys": "stem toy",
        "sltmobitel merchandise": "merchandise",
        "laptops": "laptop",
        "network switches": "network switch",
    }

    if "smart display" in name_l:
        return "smart display"

    if "smart speaker" in name_l:
        return "smart speaker"

    if "cordless" in name_l and ("phone" in name_l or "telephone" in name_l):
        return "cordless phone"

    if "caller id" in name_l or "cli" in name_l:
        return "caller id telephone"

    if "power guard" in name_l:
        return "power guard"

    if "ups" in name_l or "power backup" in name_l:
        return "power backup"

    if "antenna" in name_l:
        return "antenna"

    if "router" in name_l:
        return "router"

    if "switch" in name_l:
        return "network switch"

    if "extender" in name_l:
        return "wifi extender"

    if "adapter" in name_l or "adaptor" in name_l:
        return "adapter"

    if "laptop" in name_l:
        return "laptop"

    if "catapult" in name_l:
        return "stem toy"

    if "telephone" in name_l:
        return "telephone"

    if "phone" in name_l and category_l in {
        "telephones",
        "cordless telephones",
        "cli/sms telephones",
    }:
        return category_defaults.get(category_l, "")

    return category_defaults.get(category_l, "")


def infer_tags(name: str, category: str, description: str, product_type: str) -> list[str]:
    """
    Keep tags as a property on Product, not as Tag nodes.
    This avoids too many nodes and relationships.
    """
    name_l = name.lower()
    category_l = category.lower()
    desc_l = description.lower()

    tags = set()

    category_tags = {
        "smart home corner": {"smart home"},
        "power guards": {"power protection"},
        "power backups": {"backup power"},
        "cordless telephones": {"cordless"},
        "cli/sms telephones": {"caller id"},
        "3g & 4g routers": {"connectivity"},
        "stem toys": {"stem toy"},
        "network switches": {"networking"},
        "laptops": {"computer"},
        "wi-fi adaptors": {"wifi"},
        "access points & extenders": {"wifi", "networking"},
    }

    tags.update(category_tags.get(category_l, set()))

    allowed_product_type_tags = {
        "router",
        "power backup",
        "power guard",
        "cordless phone",
        "caller id telephone",
        "smart display",
        "smart speaker",
        "network switch",
        "laptop",
        "stem toy",
        "antenna",
        "wifi adaptor",
        "wifi extender",
        "adapter",
    }

    if product_type in allowed_product_type_tags:
        tags.add(product_type)

    if "alexa" in name_l:
        tags.add("voice assistant")

    if category_l == "smart home corner":
        if "bluetooth" in desc_l:
            tags.add("bluetooth")
        if "wifi" in desc_l or "wi-fi" in desc_l:
            tags.add("wifi")
        if "camera" in desc_l:
            tags.add("camera")
        if "screen" in desc_l or "touchscreen" in desc_l or "display" in name_l:
            tags.add("display")

    if category_l == "3g & 4g routers":
        if "4g" in name_l or "4g" in desc_l:
            tags.add("4g")
        if "lte" in name_l or "lte" in desc_l:
            tags.add("lte")
        if "wifi" in desc_l or "wi-fi" in desc_l:
            tags.add("wifi")

    if category_l == "network switches":
        if "poe" in name_l or "poe" in desc_l:
            tags.add("poe")
        if "gigabit" in name_l or "gigabit" in desc_l:
            tags.add("gigabit")

    if category_l in {"telephones", "cordless telephones", "cli/sms telephones"}:
        if "handsfree" in desc_l or "hands-free" in desc_l:
            tags.add("handsfree")
        if "sms" in desc_l or "sms" in name_l:
            tags.add("sms")

    return sorted(tags)


def extract_specs(description: str) -> dict[str, str]:
    """
    Store specs as a JSON-like Product property instead of Spec nodes.
    This keeps the graph small and avoids unnecessary relationship explosion.
    """
    specs: dict[str, str] = {}

    if not description:
        return specs

    normalized = description.replace("–", "-").replace("—", "-")

    known_key_map = {
        "nominal voltage": "nominal_voltage",
        "rated voltage": "rated_voltage",
        "rated output": "rated_output",
        "apparent power": "apparent_power",
        "startup delay": "startup_delay",
        "frequency range": "frequency_range",
        "connector type": "connector_type",
        "material": "material",
        "actual gain": "actual_gain",
        "cable type": "cable_type",
        "antenna type": "antenna_type",
        "lightning protection": "lightning_protection",
        "support pole diameter": "support_pole_diameter",
        "power input": "power_input",
        "output": "output",
        "size": "size",
        "weight": "weight",
        "warranty": "warranty",
    }

    pair_patterns = [
        r"([A-Za-z][A-Za-z0-9 /&().+-]{1,40})\s*:\s*([^\n.;]{1,100})",
        r"([A-Za-z][A-Za-z0-9 /&().+-]{1,40})\s*-\s*([^\n.;]{1,100})",
    ]

    for pattern in pair_patterns:
        for match in re.finditer(pattern, normalized, re.IGNORECASE):
            raw_key = clean_text(match.group(1)).lower()
            raw_value = clean_text(match.group(2))

            if raw_key not in known_key_map:
                continue

            if len(raw_value.split()) > 12:
                continue

            specs[known_key_map[raw_key]] = raw_value

    extra_patterns = {
        "camera": r"\b(\d+\s*MP camera)\b",
        "screen_size": r"\b(\d+(?:\.\d+)?[\"”]\s*(?:HD\s*)?(?:touchscreen|screen))\b",
        "battery": r"\b(\d+\s*cell Li-Ion battery|18650\s*mAH)\b",
        "warranty": r"\b(\d+\s*(?:month|months|year|years)\s*(?:manufacture\s*)?warranty(?:\s*period)?)\b",
        "output": r"\b(\d+V\s*\d+mA output)\b",
    }

    for key, pattern in extra_patterns.items():
        match = re.search(pattern, normalized, re.IGNORECASE)

        if match and key not in specs:
            specs[key] = clean_text(match.group(1))

    return specs


def enrich_product(product: dict) -> dict:
    name = product.get("name", "")
    category = product.get("category", "")
    description = product.get("description", "")
    seller = product.get("seller", "")

    brand = infer_brand(name, seller, category)
    product_type = infer_product_type(name, category, description)
    tags = infer_tags(name, category, description, product_type)
    specs = extract_specs(description)

    product["brand"] = brand
    product["product_type"] = product_type
    product["tags"] = tags
    product["specs"] = specs

    return product


def scrape_product_page(url: str, product_id: str) -> dict:
    html = fetch_page(url)
    soup = BeautifulSoup(html, "html.parser")
    lines = get_lines(soup)

    name = extract_name(lines, soup)
    category = extract_category(lines, name)
    price_value = extract_price(lines, name)
    stock = extract_stock(lines, name)
    description = extract_description(lines)
    image_result = extract_best_product_image_from_soup(
        soup=soup,
        product_url=url,
        expected_name=name,
    )
    image_url = image_result.get("image_url", "")
    seller = extract_seller(lines)

    product = {
        "product_id": product_id,
        "sku": "",
        "name": name,
        "description": description,
        "price": format_price(price_value),
        "price_value": price_value,
        "currency": "LKR",
        "stock": stock,
        "stock_status": extract_stock_status(stock),
        "url": url,
        "image_url": image_url,
        "image_source": "graph_builder_integrated_scraper_logic" if image_url else "image_not_found",
        "image_score": image_result.get("score"),
        "image_reason": image_result.get("reason") or image_result.get("status", ""),
        "brand": "",
        "category": category or "Uncategorized",
        "seller": seller,
        "product_type": "",
        "tags": [],
        "specs": {},
        "source": "lifestore",
    }

    return enrich_product(product)


# ---------------------------------------------------------------------
# Neo4j write logic
# ---------------------------------------------------------------------
def create_constraints(driver):
    queries = [
        "CREATE CONSTRAINT product_id_unique IF NOT EXISTS FOR (p:Product) REQUIRE p.product_id IS UNIQUE",
        "CREATE CONSTRAINT brand_name_unique IF NOT EXISTS FOR (b:Brand) REQUIRE b.name IS UNIQUE",
        "CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
        "CREATE CONSTRAINT availability_status_unique IF NOT EXISTS FOR (a:Availability) REQUIRE a.status IS UNIQUE",
        "CREATE INDEX product_name_index IF NOT EXISTS FOR (p:Product) ON (p.name)",
        "CREATE INDEX product_url_index IF NOT EXISTS FOR (p:Product) ON (p.url)",
        "CREATE INDEX product_type_index IF NOT EXISTS FOR (p:Product) ON (p.product_type)",
        "CREATE INDEX product_source_index IF NOT EXISTS FOR (p:Product) ON (p.source)",
    ]

    with driver.session(database=NEO4J_DATABASE) as session:
        for query in queries:
            session.run(query)

    print("Neo4j constraints/indexes ready.")


def clear_lifestore_graph(driver):
    """
    Safer than MATCH (n) DETACH DELETE n.

    Deletes Product nodes and then removes dangling Brand/Category/Availability
    nodes that are no longer connected to anything.
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run("MATCH (p:Product) DETACH DELETE p")
        session.run(
            """
            MATCH (n)
            WHERE NOT (n)--()
              AND any(label IN labels(n) WHERE label IN ["Brand", "Category", "Availability"])
            DELETE n
            """
        )

    print("Existing LifeStore product graph cleared.")


def cleanup_dangling_lookup_nodes(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(
            """
            MATCH (n)
            WHERE NOT (n)--()
              AND any(label IN labels(n) WHERE label IN ["Brand", "Category", "Availability"])
            DELETE n
            """
        )


def upsert_product(driver, product: dict):
    """
    Clean graph structure:

    Product -> Brand
    Product -> Category
    Product -> Availability

    Tags and specs are stored as Product properties.
    """
    cypher = """
    MERGE (p:Product {product_id: $product_id})
    SET
        p.sku = $sku,
        p.name = $name,
        p.description = $description,
        p.price = $price,
        p.price_value = $price_value,
        p.currency = $currency,
        p.stock = $stock,
        p.stock_status = $stock_status,
        p.url = $url,
        p.image_url = $image_url,
        p.image_source = $image_source,
        p.image_score = $image_score,
        p.image_reason = $image_reason,
        p.seller = $seller,
        p.product_type = $product_type,
        p.tags = $tags,
        p.specs_json = $specs_json,
        p.source = "lifestore",
        p.missing_from_latest_scrape = false,
        p.updated_at = datetime(),
        p.last_seen_at = datetime()

    WITH p
    OPTIONAL MATCH (p)-[old_brand:MADE_BY]->(:Brand)
    DELETE old_brand

    WITH p
    OPTIONAL MATCH (p)-[old_category:BELONGS_TO]->(:Category)
    DELETE old_category

    WITH p
    OPTIONAL MATCH (p)-[old_availability:HAS_AVAILABILITY]->(:Availability)
    DELETE old_availability

    WITH p
    MERGE (b:Brand {name: $brand})
    MERGE (p)-[:MADE_BY]->(b)

    MERGE (c:Category {name: $category})
    MERGE (p)-[:BELONGS_TO]->(c)

    MERGE (a:Availability {status: $stock_status})
    MERGE (p)-[:HAS_AVAILABILITY]->(a)
    """

    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(
            cypher,
            product_id=product["product_id"],
            sku=product["sku"],
            name=product["name"],
            description=product["description"],
            price=product["price"],
            price_value=product["price_value"],
            currency=product["currency"],
            stock=product["stock"],
            stock_status=product["stock_status"],
            url=product["url"],
            image_url=product.get("image_url", ""),
            image_source=product.get("image_source", ""),
            image_score=product.get("image_score"),
            image_reason=product.get("image_reason", ""),
            seller=product["seller"],
            product_type=product["product_type"],
            tags=product["tags"],
            specs_json=json.dumps(product["specs"], ensure_ascii=False),
            brand=product["brand"] or "Unknown",
            category=product["category"] or "Uncategorized",
        )


def mark_or_remove_missing_products(driver, current_product_ids: list[str]):
    """
    Handles products that were in Neo4j before but were not found in the latest scrape.

    Default:
    - Mark them as missing/out_of_stock.

    If REMOVE_MISSING_LIFESTORE_PRODUCTS=true:
    - Remove them from the graph.
    """
    if not current_product_ids:
        print("No current product IDs supplied; skipping missing-product cleanup.")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        if REMOVE_MISSING_PRODUCTS:
            result = session.run(
                """
                MATCH (p:Product {source: "lifestore"})
                WHERE NOT p.product_id IN $current_product_ids
                DETACH DELETE p
                RETURN count(p) AS removed_count
                """,
                current_product_ids=current_product_ids,
            ).single()

            removed_count = result["removed_count"] if result else 0
            print(f"Removed missing LifeStore products: {removed_count}")

        else:
            result = session.run(
                """
                MATCH (p:Product {source: "lifestore"})
                WHERE NOT p.product_id IN $current_product_ids
                SET
                    p.missing_from_latest_scrape = true,
                    p.stock_status = "out_of_stock",
                    p.stock = 0,
                    p.last_missing_at = datetime()
                RETURN count(p) AS marked_count
                """,
                current_product_ids=current_product_ids,
            ).single()

            marked_count = result["marked_count"] if result else 0
            print(f"Marked missing LifeStore products as out_of_stock: {marked_count}")

    cleanup_dangling_lookup_nodes(driver)


def print_graph_counts(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        print("\n--- Node counts ---")
        for label in ["Product", "Brand", "Category", "Availability"]:
            count = session.run(
                f"MATCH (n:{label}) RETURN count(n) AS count"
            ).single()["count"]
            print(f"{label}: {count}")

        print("\n--- LifeStore product counts ---")
        result = session.run(
            """
            MATCH (p:Product {source: "lifestore"})
            RETURN
                count(p) AS total,
                count(CASE WHEN p.missing_from_latest_scrape = true THEN 1 END) AS missing
            """
        ).single()

        if result:
            print(f"LifeStore products: {result['total']}")
            print(f"Missing from latest scrape: {result['missing']}")

        print("\n--- Availability counts ---")
        result = session.run(
            """
            MATCH (p:Product {source: "lifestore"})-[:HAS_AVAILABILITY]->(a:Availability)
            RETURN a.status AS status, count(p) AS count
            ORDER BY count DESC
            """
        )

        for row in result:
            print(f"{row['status']}: {row['count']}")

        print("\n--- Relationship counts ---")
        result = session.run(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS relationship, count(r) AS count
            ORDER BY count DESC
            """
        )

        for row in result:
            print(f"{row['relationship']}: {row['count']}")

def print_image_source_summary(products: list[dict]) -> None:
    """
    Print how product image URLs were selected in the integrated graph builder.
    """
    source_counts: dict[str, int] = {}

    for product in products:
        source = clean_text(product.get("image_source")) or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

    print("\n--- Image source counts ---")
    for source, count in sorted(source_counts.items(), key=lambda item: item[0]):
        print(f"{source}: {count}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    if not NEO4J_URI or not NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD must be set.")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
    )

    try:
        driver.verify_connectivity()
        print("Connected to Neo4j.")

        if CLEAR_GRAPH:
            clear_lifestore_graph(driver)

        create_constraints(driver)

        product_urls = scrape_listing_pages()

        if not product_urls:
            raise RuntimeError("No LifeStore product URLs were found. Graph update stopped.")

        products = []
        failed = []
        total = len(product_urls)

        for idx, product_url in enumerate(product_urls, start=1):
            print(f"Scraping product {idx}/{total}: {product_url}")

            try:
                product_id = make_stable_product_id(product_url)
                product = scrape_product_page(product_url, product_id)
                print(
                    f"  id={product['product_id']!r}, "
                    f"name={product['name']!r}, "
                    f"price={product['price']!r}, "
                    f"stock={product['stock_status']!r}, "
                    f"brand={product['brand']!r}, "
                    f"category={product['category']!r}, "
                    f"type={product['product_type']!r}, "
                    f"image_source={product.get('image_source')!r}, "
                    f"image={product['image_url']!r}"
                )

                products.append(product)
                upsert_product(driver, product)

                time.sleep(REQUEST_DELAY_SECONDS)

            except Exception as exc:
                print(f"  failed: {product_url} -> {exc}")
                failed.append(
                    {
                        "url": product_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        if not products:
            raise RuntimeError("Product URLs were found, but no products were successfully scraped.")

        bad_image_products = [
            product for product in products
            if product.get("image_url") and is_bad_lifestore_image(product.get("image_url", ""))
        ]

        missing_image_products = [
            product for product in products
            if not product.get("image_url")
        ]

        unique_image_urls = sorted(
            {
                product.get("image_url")
                for product in products
                if product.get("image_url")
            }
        )

        print("\n--- Image extraction check ---")
        print(f"Products scraped: {len(products)}")
        print(f"Unique image URLs found: {len(unique_image_urls)}")
        print(f"Products with banner/header image: {len(bad_image_products)}")
        print(f"Products with missing image: {len(missing_image_products)}")

        if bad_image_products:
            print("First products still using banner image:")
            for product in bad_image_products[:10]:
                print(product.get("name"), "->", product.get("url"))

        if missing_image_products:
            print("First products with missing image:")
            for product in missing_image_products[:10]:
                print(product.get("name"), "->", product.get("url"))

        print_image_source_summary(products)

        current_product_ids = [product["product_id"] for product in products]
        mark_or_remove_missing_products(driver, current_product_ids)

        output_data = {
            "source": "lifestore",
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_urls": START_URLS,
            "product_count": len(products),
            "failed_count": len(failed),
            "products": products,
            "failed": failed,
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"\nSaved {len(products)} products to {OUTPUT_FILE}")

        if failed:
            print(f"Failed products: {len(failed)}")
            print("First few failures:")
            for item in failed[:5]:
                print(item)

        print_graph_counts(driver)

    finally:
        driver.close()


if __name__ == "__main__":
    main()