import os

import requests
from crewai.tools import tool


@tool("Search trending products")
def search_trending_products(niche: str) -> str:
    """Look up trending/viral product candidates for a given niche.

    This is a stub. Wire it to a real trend source before trusting its
    output: Google Trends (pytrends), TikTok Creative Center, or a
    supplier's bestseller endpoint (CJ Dropshipping / Zendrop / Spocket
    all expose one).
    """
    return (
        f"[STUB DATA for niche='{niche}'] No live trend source connected. "
        "Replace search_trending_products() in tools.py with a real API call."
    )


@tool("Compare suppliers")
def compare_suppliers(product_name: str) -> str:
    """Compare 2-3 suppliers for a product on price, shipping time and rating.

    This is a stub. Wire it to the CJ Dropshipping / Zendrop / Spocket API.
    """
    return f"[STUB DATA for product='{product_name}'] No supplier API connected."


@tool("Get Shopify store snapshot")
def get_shopify_snapshot() -> str:
    """Fetch a recent orders / traffic summary from the connected Shopify store."""
    domain = os.getenv("SHOPIFY_STORE_DOMAIN")
    token = os.getenv("SHOPIFY_ADMIN_API_TOKEN")
    if not domain or not token:
        return (
            "Shopify is not connected yet — set SHOPIFY_STORE_DOMAIN and "
            "SHOPIFY_ADMIN_API_TOKEN in .env."
        )
    return (
        "[STUB] Replace get_shopify_snapshot() with a real call to the "
        f"Shopify Admin API on {domain} (e.g. /admin/api/2026-01/orders.json)."
    )


@tool("Send approval request to the business owner")
def send_approval_request(summary: str) -> str:
    """Send a yes/no budget or product-decision request to the human owner.

    Posts to APPROVAL_WEBHOOK_URL (a Slack Incoming Webhook, or an n8n
    Webhook node that forwards to WhatsApp/email). Prints locally if no
    webhook is configured, so the crew never crashes for lack of one.
    """
    webhook = os.getenv("APPROVAL_WEBHOOK_URL")
    if not webhook:
        print(f"[APPROVAL NEEDED — no webhook configured]\n{summary}")
        return "No APPROVAL_WEBHOOK_URL configured; printed locally instead."
    try:
        requests.post(webhook, json={"text": summary}, timeout=10)
        return "Approval request sent."
    except requests.RequestException as exc:
        print(f"[APPROVAL NEEDED — webhook failed: {exc}]\n{summary}")
        return f"Failed to send approval request ({exc}); printed locally instead."
