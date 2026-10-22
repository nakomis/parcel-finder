"""What is in the photo? Local OCR, a few rules, then Haiku."""
import base64
import json
import re
import subprocess
import tempfile
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
    description: str = ""  # Haiku's long description; `item` is the short name used everywhere else


# GLM-OCR has a 4096-token context: a full-resolution ScreenEye picture (2160×3840) overflows it.
OCR_MAX_DIM = 1600


def _downscaled(photo: Path) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "ocr.jpg"
        r = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(OCR_MAX_DIM), str(photo), "--out", str(out)],
                           capture_output=True)
        return out.read_bytes() if r.returncode == 0 and out.exists() else photo.read_bytes()


def ocr(photo: Path) -> str | None:
    body = json.dumps({"model": config.OCR_MODEL, "prompt": config.OCR_PROMPT, "stream": False,
                       "images": [base64.b64encode(_downscaled(photo)).decode()]}).encode()
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


_GENERIC = {"PWR", "RESET", "BOOT", "GND", "VCC", "3V", "5V", "3V3",
            # keyboard keys, when the photo catches a MacBook
            "CONTROL", "OPTION", "COMMAND", "SHIFT", "RETURN", "DELETE", "ENTER", "CAPS", "LOCK", "ESC"}


def is_informative(text: str | None) -> bool:
    if not text:
        return False
    toks = tokens(text)
    return len(toks) >= 4 and bool(toks - _GENERIC)


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "short product name, at most 6 words"},
        "item": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
    },
    "required": ["name", "item", "confidence", "evidence"],
    "additionalProperties": False,
}

PROMPT = ("This is a photo of an electronics item that has just arrived in a parcel. Give a short name "
          "for it (at most 6 words, like a shop listing title, e.g. \"SOP-24 to DIP adapter board\"), then "
          "describe what it is and what it is for.\n{hint}"
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
    return Identification(out["name"], out["confidence"], out["evidence"], source, text, out["item"])


def search_terms(ident: Identification, max_words: int = 5) -> str:
    """The short name plus the most distinctive legible words (part numbers, markings): sharper
    conversation-memory queries than a long description."""
    words = []
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*", ident.ocr_text or ""):
        if len(w) >= 4 and re.search(r"[A-Za-z]", w) and w.upper() not in _GENERIC and w not in words:
            words.append(w)
    return " ".join([ident.item] + words[:max_words])
