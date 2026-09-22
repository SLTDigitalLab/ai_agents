"""On-demand LifeStore stock verification; never reads or updates the catalog."""
import re
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup


def unverified(reason):
    return {"stock": None, "stock_status": "unknown", "availability": "unknown",
            "availability_verified": False, "source": "live_website",
            "availability_error": reason}


def valid_product_url(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in {"lifestore.lk", "www.lifestore.lk"}
                and parts.port in {None, 443} and not parts.username and not parts.password
                and re.fullmatch(r"/product/[A-Za-z0-9_-]+/?", parts.path) is not None
                and not parts.query and not parts.fragment)
    except (ValueError, TypeError):
        return False


def check_live_availability(product_url):
    if not valid_product_url(product_url):
        return unverified("invalid_product_url")
    try:
        # No redirects/retries: never follow a product URL to a listing or another host.
        response = httpx.get(product_url, timeout=httpx.Timeout(5.0, connect=2.0),
                             follow_redirects=False, headers={"User-Agent": "LifeStore-Availability/1.0"})
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").lower():
            return unverified("unexpected_page")
        soup = BeautifulSoup(response.text, "html.parser")
        lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines()]
        lines = [line for line in lines if line]
        # Reuse the ingestion parser's breadcrumb/name and bounded Quantity rule.
        # Require a complete product section first: missing markup is NOT sold out.
        start = next((i + 2 for i in range(len(lines) - 2)
                      if lines[i:i + 2] == ["Home", "Products"]), None)
        if start is None:
            return unverified("unexpected_page")
        boundaries = {"Product Description :", "Related Products", "Overview", "Specification"}
        end = next((i for i in range(start + 1, len(lines)) if lines[i] in boundaries), None)
        if end is None or not any(re.search(r"Rs\.?\s*[\d,]+", x) for x in lines[start:end]):
            return unverified("incomplete_product_page")
        stock = int("Quantity" in lines[start:end])
        status = "in_stock" if stock else "out_of_stock"
        return {"stock": stock, "stock_status": status, "availability": status,
                "availability_verified": True, "source": "live_website"}
    except (httpx.HTTPError, ValueError):
        return unverified("website_request_failed")
