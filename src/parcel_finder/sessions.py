"""Which Claude Code session decided to buy this? conversation-memory search, then Haiku chooses."""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, haiku
from .identify import Identification
from .orders.model import OrderMatch

BEFORE_ORDER = timedelta(hours=72)
NEAR_ORDER_BOOST = 3.0
CLOSE_RUNNER_UP = 0.75  # runner-up within 25 % of the winner means "not clear"


@dataclass
class Candidate:
    session_id: str
    title: str
    project: str
    when: datetime
    hits: int
    snippet: str
    score: float = 0.0


@dataclass
class SessionChoice:
    chosen: Candidate | None
    clear: bool
    candidates: list[Candidate]
    reason: str


# Runs inside conversation-memory's own project, the way ~/scripts/cjump does.
_SEARCH = r'''
import json, logging, os, sys, tomllib
logging.disable(logging.CRITICAL)
cfg = tomllib.load(open(sys.argv[2], "rb"))
for m in cfg["mcps"]:
    if m["name"] == "conversation-memory":
        os.environ.update(m.get("env", {}))
from conversation_memory_mcp.server import search_memory
r = search_memory(query=sys.argv[1], limit=20, since="all")
r = json.loads(r) if isinstance(r, str) else r
print(json.dumps(r.get("results", [])))
'''


def _raw_search(query: str) -> list[dict]:
    r = subprocess.run(["uv", "run", "-q", "--project", str(config.CONV_MEM_PROJECT), "python", "-", query,
                        str(config.META_MCP_CONFIG)], input=_SEARCH, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(f"conversation-memory search failed: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def rank(results: list[dict], around: datetime | None) -> list[Candidate]:
    by_id: dict[str, Candidate] = {}
    for x in results:
        when = datetime.fromisoformat(x["created_at_utc"].replace("Z", "+00:00"))
        c = by_id.get(x["session_id"])
        if not c:
            snippet = next((" ".join(str(m.get("content", "")).split())[:200] for m in x.get("context", [])
                            if m.get("content") and not str(m["content"]).startswith("{")), "")
            c = by_id[x["session_id"]] = Candidate(x["session_id"], x.get("name") or x.get("ai_title") or "",
                                                   x.get("project_path", ""), when, 0, snippet)
        c.hits += 1
        c.score += 1.0
        c.when = max(c.when, when)
        if around and around - BEFORE_ORDER <= when <= around + timedelta(hours=1):
            c.score += NEAR_ORDER_BOOST
    return sorted(by_id.values(), key=lambda c: c.score, reverse=True)


def search(query: str, around: datetime | None) -> list[Candidate]:
    return rank(_raw_search(query), around)


SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["session_id", "confidence", "reason"],
    "additionalProperties": False,
}


def choose(ident: Identification, match: OrderMatch | None, candidates: list[Candidate]) -> SessionChoice:
    if not candidates:
        return SessionChoice(None, False, [], "no candidate sessions")
    top = candidates[:8]
    bought = (f"It matches the order \"{match.order.title}\" ({match.order.shop}, ordered "
              f"{match.order.ordered_at:%d %b %Y %H:%M} UTC).\n" if match else "")
    listing = "\n".join(f"- {c.session_id} | {c.title} | {c.project} | last hit {c.when:%d %b %Y} | "
                        f"{c.hits} hits | match score {c.score:.0f} | {c.snippet}" for c in top)
    prompt = (f"Martin photographed a parcel that has just arrived: \"{ident.item}\".\n{bought}"
              "These Claude Code sessions mention it (the match score counts matching messages, boosted near "
              "the order time). Pick the session where this specific product was recommended, chosen or put in "
              "a basket: usually the one with the strongest match, close to the order date. General background "
              "research months earlier, or a later session that only talks about the parcel, is not it. "
              "Use an empty session_id if none of them plausibly is.\n\n" + listing)
    out = haiku.ask_json(prompt, [], SCHEMA, max_tokens=800)
    chosen = next((c for c in top if c.session_id == out["session_id"]), None)
    if not chosen:
        return SessionChoice(None, False, top, out["reason"])
    others = [c.score for c in top if c is not chosen]
    clear = (match is not None and out["confidence"] == "high"
             and (not others or max(others) < CLOSE_RUNNER_UP * chosen.score))
    return SessionChoice(chosen, clear, top, out["reason"])


def local_transcript(session_id: str) -> Path | None:
    return next(config.PROJECTS_DIR.glob(f"*/{session_id}.jsonl"), None)
