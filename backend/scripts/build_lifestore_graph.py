import json
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from neo4j import GraphDatabase


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
BASE_URL = "https://lifestore.lk"
START_PATH = "/categories"

OUTPUT_FILE = Path("data/real/lifestore_all.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

REQUEST_DELAY_SECONDS = float(os.getenv("LIFESTORE_GRAPH_DELAY", "0.5"))
CLEAR_GRAPH = os.getenv("CLEAR_LIFESTORE_GRAPH", "false").lower() == "true"

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


# ---------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------
def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


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


# ---------------------------------------------------------------------
# Listing page crawler
# ---------------------------------------------------------------------
def extract_product_urls(soup: BeautifulSoup) -> list[str]:
    urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()

        if "/product/" not in href:
            continue

        full_url = urljoin(BASE_URL, href)

        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    return urls


def extract_next_page_url(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" "))
        href = a.get("href", "").strip()

        if "Next page" in text and href:
            return urljoin(BASE_URL, href)

    return None


def scrape_listing_pages() -> list[str]:
    url = urljoin(BASE_URL, START_PATH)
    all_urls = []
    seen_products = set()
    visited_pages = set()
    page_num = 1

    while url and url not in visited_pages:
        visited_pages.add(url)

        print(f"Scraping listing page {page_num}: {url}")

        html = fetch_page(url)
        soup = BeautifulSoup(html, "html.parser")

        product_urls = extract_product_urls(soup)
        print(f"  found product urls: {len(product_urls)}")

        for product_url in product_urls:
            if product_url not in seen_products:
                seen_products.add(product_url)
                all_urls.append(product_url)

        next_url = extract_next_page_url(soup)
        print(f"  next page: {next_url}")

        if not next_url or next_url in visited_pages:
            break

        url = next_url
        page_num += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Total unique product URLs: {len(all_urls)}")
    return all_urls


# ---------------------------------------------------------------------
# Product page extraction logic from your previous working scraper
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
    This is the important LifeStore-specific logic from your old scraper.

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


def extract_image_url(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:image")

    if og and og.get("content"):
        return urljoin(BASE_URL, og["content"])

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")

        if src:
            return urljoin(BASE_URL, src)

    return ""


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
    image_url = extract_image_url(soup)
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
        "brand": "",
        "category": category or "Uncategorized",
        "seller": seller,
        "product_type": "",
        "tags": [],
        "specs": {},
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
    ]

    with driver.session(database=NEO4J_DATABASE) as session:
        for query in queries:
            session.run(query)

    print("Neo4j constraints/indexes ready.")


def clear_graph(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run("MATCH (n) DETACH DELETE n")

    print("Existing graph cleared.")


def upsert_product(driver, product: dict):
    """
    Minimal clean graph:
    Product -> Brand
    Product -> Category
    Product -> Availability

    No Tag nodes.
    No Spec nodes.
    Specs/tags are stored as Product properties.
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
        p.seller = $seller,
        p.product_type = $product_type,
        p.tags = $tags,
        p.specs_json = $specs_json,
        p.updated_at = datetime()

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
            image_url=product["image_url"],
            seller=product["seller"],
            product_type=product["product_type"],
            tags=product["tags"],
            specs_json=json.dumps(product["specs"], ensure_ascii=False),
            brand=product["brand"] or "Unknown",
            category=product["category"] or "Uncategorized",
        )


def print_graph_counts(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        print("\n--- Node counts ---")
        for label in ["Product", "Brand", "Category", "Availability"]:
            count = session.run(
                f"MATCH (n:{label}) RETURN count(n) AS count"
            ).single()["count"]
            print(f"{label}: {count}")

        print("\n--- Availability counts ---")
        result = session.run(
            """
            MATCH (p:Product)-[:HAS_AVAILABILITY]->(a:Availability)
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

    driver.verify_connectivity()
    print("Connected to Neo4j.")

    if CLEAR_GRAPH:
        clear_graph(driver)

    create_constraints(driver)

    product_urls = scrape_listing_pages()

    products = []
    total = len(product_urls)

    for idx, product_url in enumerate(product_urls, start=1):
        print(f"Scraping product {idx}/{total}: {product_url}")

        try:
            product = scrape_product_page(product_url, f"LS{idx:04d}")

            print(
                f"  name={product['name']!r}, "
                f"price={product['price']!r}, "
                f"stock={product['stock_status']!r}, "
                f"brand={product['brand']!r}, "
                f"category={product['category']!r}, "
                f"type={product['product_type']!r}"
            )

            products.append(product)
            upsert_product(driver, product)

            time.sleep(REQUEST_DELAY_SECONDS)

        except Exception as e:
            print(f"  failed: {product_url} -> {e}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(products)} products to {OUTPUT_FILE}")

    print_graph_counts(driver)
    driver.close()


if __name__ == "__main__":
    main()