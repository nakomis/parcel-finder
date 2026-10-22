"""What is in the photo? Local OCR, a few rules, then Haiku."""
import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, haiku
from .runlog import RunLog


@dataclass
class Identification:
    item: str
    confidence: str
    evidence: str
    source: str  # "rule" | "haiku" | "haiku-image-only"
    ocr_text: str | None


def ocr(photo: Path) -> str | None:
    body = json.dumps({"model": config.OCR_MODEL, "prompt": config.OCR_PROMPT, "stream": False,
                       "images": [base64.b64encode(photo.read_bytes()).decode()]}).encode()
    req = urllib.request.Request(f"{config.OLLAMA_URL}/api/generate", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["response"].strip() or None
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


# OCR confusions seen on board silkscreen (GLM-OCR, 22 Oct 2026).
_FOLD = {"GMD": "CMD", "ST": "SI", "DI": "D1", "DO": "D0"}


def tokens(text: str) -> set[str]:
    return {_FOLD.get(t, t) for t in re.findall(r"[A-Z0-9+\-]+", text.upper())}


# (verdict, predicate over tokens). First match wins.
RULES = [
    ("microSD breakout, SPI + 4-bit SDIO",
     lambda t: {"CLK", "CMD"} <= t and bool({"D1", "DAT1"} & t) and bool({"D2", "DAT2"} & t)),
    ("microSD breakout, SPI only",
     lambda t: "CS" in t and bool({"MISO", "SO"} & t) and bool({"MOSI", "SI"} & t)
     and not ({"D1", "DAT1", "D2", "DAT2", "CMD"} & t)),
]


def match_rule(toks: set[str]) -> str | None:
    return next((verdict for verdict, pred in RULES if pred(toks)), None)


_GENERIC = {"PWR", "RESET", "BOOT", "GND", "VCC", "3V", "5V", "3V3"}


def is_informative(text: str | None) -> bool:
    if not text:
        return False
    toks = tokens(text)
    return len(toks) >= 4 and bool(toks - _GENERIC)


SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
    },
    "required": ["item", "confidence", "evidence"],
    "additionalProperties": False,
}

PROMPT = ("This is a photo of an electronics item that has just arrived in a parcel. Say what it is and "
          "what it is for.\n{hint}"
          "Only name a maker, model or chip when its name is clearly legible in the photo; never infer a chip "
          "from a logo, a blurred marking or the board's shape. Give the visible text or features that decide "
          "it, and use low confidence when you are inferring rather than reading.")


def identify(photo: Path, log: RunLog) -> Identification:
    text = ocr(photo)
    log.event("ocr", text=text, available=text is not None)
    if text:
        verdict = match_rule(tokens(text))
        if verdict:
            log.event("rule", verdict=verdict)
            return Identification(verdict, "high", "pin labels: " + " ".join(sorted(tokens(text))), "rule", text)
    informative = is_informative(text)
    hint = (f"A local OCR model read this text from it (may contain typos):\n{text}\n\n" if informative else "")
    out = haiku.ask_json(PROMPT.format(hint=hint), [photo.read_bytes()], SCHEMA)
    source = "haiku" if informative else "haiku-image-only"
    log.event("identify", source=source, **out)
    return Identification(out["item"], out["confidence"], out["evidence"], source, text)
