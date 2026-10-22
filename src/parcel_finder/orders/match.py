"""Which recent order is the thing in the photo?"""
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import haiku
from ..identify import Identification
from .model import Order, OrderMatch

MAX_TITLES = 20
MAX_THUMBNAILS = 8
STALE_DELIVERED = timedelta(days=14)
_DONE = {"delivered", "completed", "FINISH"}

SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["index", "confidence", "reason"],
    "additionalProperties": False,
}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _thumbnail(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read()
    except OSError:
        return None


def match_order(photo: Path, ident: Identification, orders: list[Order],
                now: datetime | None = None) -> OrderMatch | None:
    now = now or datetime.now(timezone.utc)
    candidates = [o for o in orders if not (o.status in _DONE and now - _aware(o.ordered_at) > STALE_DELIVERED)]
    candidates = sorted(candidates, key=lambda o: _aware(o.ordered_at), reverse=True)[:MAX_TITLES]
    if not candidates:
        return None
    images, lines = [photo.read_bytes()], []
    for i, o in enumerate(candidates):
        thumb = None
        if o.thumbnail_urls and len(images) <= MAX_THUMBNAILS:
            thumb = _thumbnail(o.thumbnail_urls[0])
        if thumb:
            images.append(thumb)
        lines.append(f"{i}. [{o.shop}, ordered {o.ordered_at:%d %b}] {o.title}"
                     + (f" (product image {len(images) - 1})" if thumb else ""))
    prompt = ("Image 0 is a photo of an item that has just arrived. A first look identified it as "
              f"\"{ident.item}\" ({ident.evidence}). Which of these recent orders is it? "
              "Later images are shop product photos where marked.\n\n" + "\n".join(lines) +
              "\n\nReply with the order's index, or -1 if none of them plausibly matches.")
    out = haiku.ask_json(prompt, images, SCHEMA)
    if not 0 <= out["index"] < len(candidates):
        return None
    return OrderMatch(candidates[out["index"]], out["confidence"], out["reason"])
