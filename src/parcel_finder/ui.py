"""Notifications and the session pick-list (osascript)."""
import json
import subprocess

from .sessions import Candidate


def _osascript(script: str, *args: str) -> str:
    r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"osascript failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def notify(title: str, text: str) -> None:
    subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title {json.dumps(title)}"],
                   capture_output=True)


_CHOOSE = '''
on run argv
  set opts to items 2 thru -1 of argv
  activate -- a background process's dialog otherwise opens behind everything
  set picked to choose from list opts with title "parcel-finder" with prompt (item 1 of argv) OK button name "Open"
  if picked is false then return "false"
  return item 1 of picked
end run'''


def choose(candidates: list[Candidate], item: str) -> str | None:
    labels = [f"{c.title or c.session_id[:8]}  ({c.when:%d %b %Y}, {c.hits} matches)" for c in candidates]
    picked = _osascript(_CHOOSE, f"Which conversation was the {item} bought in?", *labels)
    return candidates[labels.index(picked)].session_id if picked in labels else None
