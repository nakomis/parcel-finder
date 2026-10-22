"""Order emails from Gmail, through the Gmail API. All parsing is local: no model calls."""
import base64
import re
from datetime import date, datetime, timezone

from .model import Order

# (status, pattern) per shop; the pattern's last group is the title (or the order number for AliExpress).
_AMAZON = [("ordered", r"^Ordered:"), ("shipped", r"^(Dispatched|Out for delivery|Shipped):"),
           ("delivered", r"^Delivered:"), ("cancelled", r"^(Cancelled|Canceled|Your order .* cancelled)")]
_EBAY = [("ordered", r"order confirmed"), ("shipped", r"order update|out for delivery|shipped|dispatched"),
         ("delivered", r"package delivered|delivered"), ("cancelled", r"cancel")]
_AE_STATUS = [("ordered", r"order confirmed|payment"), ("delivered", r"awaiting confirmation|how did it go"),
              ("completed", r"completed"), ("cancelled", r"cancel|closed")]


def _clean_title(s: str) -> str:
    s = re.sub(r"\s+and \d+ more items?$", "", s.strip())
    s = re.sub(r"^\d+\s+(?=[‘'\"“])", "", s)                # "2 ‘48 Pack…’": the quantity
    s = s.strip("‘’'\"“” ")
    s = re.sub(r"(\.\.\.|…)$", "", s).strip()
    return s.rstrip(":;,- ").strip()


def parse_subject(sender: str, subject: str) -> tuple[str, str, str, str] | None:
    """(shop, order_id, title, status) for an order email, or None."""
    sender = sender.lower()
    if sender.endswith("amazon.co.uk") or sender.endswith("amazon.com"):
        for status, pat in _AMAZON:
            if re.search(pat, subject, re.I):
                title = _clean_title(subject.split(":", 1)[1]) if ":" in subject else ""
                return ("amazon", "", title, status) if title else None
        return None
    if sender.endswith("ebay.com") or sender.endswith("ebay.co.uk"):
        m = re.match(r"^\W*([^:]+):\s*(.+)$", subject)
        if not m:
            return None
        head = m.group(1).lower()
        for status, pat in _EBAY:
            if re.search(pat, head):
                title = _clean_title(m.group(2))
                return ("ebay", "", title, status) if title else None
        return None
    if "aliexpress" in sender:
        m = re.match(r"^Order (\d{10,}):\s*(.+)$", subject)
        if not m:
            return None
        rest = m.group(2).lower()
        status = next((s for s, pat in _AE_STATUS if re.search(pat, rest)), "shipped")
        return ("aliexpress", m.group(1), "", status)
    return None


_THUMB = re.compile(r"""https://[a-z0-9.-]*(?:alicdn\.com|aliexpress-media\.com)/kf/[^"'\s<>)]+""", re.I)


def extract_thumbnails(html: str, limit: int = 3) -> list[str]:
    return list(dict.fromkeys(_THUMB.findall(html)))[:limit]


def _html(payload: dict) -> str:
    parts, out = [payload], []
    while parts:
        p = parts.pop()
        parts += p.get("parts", [])
        if p.get("mimeType") == "text/html" and p.get("body", {}).get("data"):
            out.append(base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace"))
    return "\n".join(out)


def _client():
    from ..google import Gmail
    return Gmail()


def _same(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)  # subjects truncate titles at different lengths


def recent_orders(after: date, before: date) -> tuple[list[Order], dict[str, list[str]]]:
    """Orders emailed in [after, before), one per order: the earliest email's time, the latest status.
    AliExpress orders carry no title; their IDs map to product thumbnails from the emails."""
    gm = _client()
    seen: list[tuple[datetime, tuple[str, str, str, str], str]] = []
    for mid in gm.search(f"category:purchases after:{after:%Y/%m/%d} before:{before:%Y/%m/%d}"):
        msg = gm.message(mid)
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = re.sub(r".*<([^>]+)>.*", r"\1", headers.get("from", ""))
        parsed = parse_subject(sender, headers.get("subject", ""))
        if parsed:
            when = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc)
            seen.append((when, parsed, mid))
    orders: list[Order] = []
    ae: dict[str, list[str]] = {}
    for when, (shop, oid, title, status), mid in sorted(seen, key=lambda x: x[0]):
        match = next((o for o in orders if o.shop == shop and (
            (oid and o.order_id == oid) or (not oid and _same(o.title, title)))), None)
        if match:
            match.status = status                                    # later email, later status
            if len(title) > len(match.title):
                match.title = title
        else:
            orders.append(Order(shop, oid, title, when, status))
        if shop == "aliexpress" and not ae.get(oid):
            ae[oid] = extract_thumbnails(_html(gm.message(mid, full=True).get("payload", {})))
    orders = [o for o in orders if o.status != "cancelled"]
    for o in orders:
        if o.shop == "aliexpress":
            o.thumbnail_urls = ae.get(o.order_id, [])
    ae = {oid: urls for oid, urls in ae.items() if any(o.order_id == oid for o in orders)}
    return orders, ae
