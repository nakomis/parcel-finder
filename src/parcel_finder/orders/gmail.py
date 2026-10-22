"""Order emails from Gmail, read through a headless `claude -p` (the Gmail connector lives there)."""
import json
import subprocess
import tempfile
from datetime import date, datetime, timezone

from .model import Order

SCHEMA = {
    "type": "object",
    "properties": {"orders": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "shop": {"type": "string", "enum": ["amazon", "ebay", "aliexpress"]},
            "order_id": {"type": "string"},
            "title": {"type": "string"},
            "ordered_at": {"type": "string"},
            "status": {"type": "string"},
            "thumbnail_urls": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["shop", "order_id", "title", "ordered_at", "status", "thumbnail_urls"],
    }}},
    "required": ["orders"],
}

PROMPT = """Use the Gmail tools (search only; never change, label, send or delete anything) to list Martin's recent orders.
Search Gmail with exactly this query: category:purchases after:{after:%Y/%m/%d} before:{before:%Y/%m/%d}
Look only at emails from Amazon, eBay and AliExpress. Return one entry per distinct order (latest status wins):
- shop: amazon, ebay or aliexpress
- order_id: the order number if shown (AliExpress subjects look like "Order 3076931683840500: ..."), else ""
- title: the product title from the subject or body (AliExpress emails often have none: use "")
- ordered_at: ISO 8601 UTC time of the order (the earliest email about it)
- status: ordered, shipped, delivered, completed or cancelled
- thumbnail_urls: for AliExpress only, up to 3 product image URLs found in the email HTML, else []"""


def recent_orders(after: date, before: date) -> tuple[list[Order], dict[str, list[str]]]:
    """Orders emailed in [after, before): Amazon/eBay with titles, AliExpress IDs mapped to thumbnails.

    Slow (minutes) and about $0.30 a week of mail: call it from the cache sync, not per photo."""
    argv = ["claude", "-p", "--model", "haiku", "--no-session-persistence", "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA), "--", PROMPT.format(after=after, before=before)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=600, cwd=tempfile.gettempdir(),
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError(f"claude -p failed: {r.stderr.strip()[:300]}")
    reply = json.loads(r.stdout)
    if "structured_output" not in reply:
        raise RuntimeError(f"claude -p returned no orders ({reply.get('subtype')}, {str(reply.get('result'))[:200]})")
    found = reply["structured_output"]["orders"]
    orders, ae = [], {}
    for o in found:
        when = datetime.fromisoformat(o["ordered_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if o["shop"] == "aliexpress":
            if o["order_id"] and o["status"] != "cancelled":
                ae[o["order_id"]] = o["thumbnail_urls"]
                # Kept (untitled) so matching can fall back on thumbnails + order time without the API.
                orders.append(Order("aliexpress", o["order_id"], "", when, o["status"],
                                    thumbnail_urls=o["thumbnail_urls"]))
            continue
        if o["status"] != "cancelled" and o["title"]:
            orders.append(Order(o["shop"], o["order_id"], o["title"], when, o["status"]))
    return orders, ae
