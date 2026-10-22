"""Get the photo and the message into the session: share copy, raise or resume, socket message."""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

MESSAGE = ("This just arrived! It's been identified as a {item}. What do I do with it? "
           "If you don't recognise it, please let me know!\n\nPhoto: {photo}")


def message(item: str, photo_path: Path) -> str:
    return MESSAGE.format(item=item, photo=photo_path)


def copy_photo(photo: Path, slug: str, now: datetime) -> tuple[Path, bool]:
    if not config.SHARE_ROOT.parent.is_dir():  # the share isn't mounted
        return photo, False
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:60] or "parcel"
    dest = config.SHARE_ROOT / f"{now:%Y-%m-%d}" / f"{now:%H%M%S}-{slug}.jpg"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(photo, dest)
    except OSError:
        return photo, False
    return dest, True


@dataclass
class LiveSession:
    pid: int
    socket: Path
    key_file: Path
    tty: str | None
    cwd: Path
    status: str


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def live_session(session_id: str, sessions_dir: Path = config.SESSIONS_DIR) -> LiveSession | None:
    for f in sessions_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if d.get("sessionId") != session_id or not _alive(int(d["pid"])):
            continue
        key = next(sessions_dir.glob(f"{d['pid']}.*.key"), None)
        if not key or not d.get("messagingSocketPath"):
            continue
        tty = subprocess.run(["ps", "-o", "tty=", "-p", str(d["pid"])], capture_output=True,
                             text=True).stdout.strip()
        return LiveSession(int(d["pid"]), Path(d["messagingSocketPath"]), key,
                           tty if tty and tty != "??" else None, Path(d.get("cwd", config.HOME)),
                           d.get("status", ""))
    return None


def send(live: LiveSession, text: str) -> None:
    """Claude Code's messaging socket: an auth line, then a user message (as midichlorian's notify.py)."""
    payload = (json.dumps({"type": "auth", "token": live.key_file.read_text().strip()}) + "\n"
               + json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(str(live.socket))
        s.sendall(payload.encode())


def _osascript(script: str, *args: str) -> str:
    r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise RuntimeError(f"osascript failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


_RAISE = '''
on run argv
  set target to item 1 of argv
  tell application "Terminal"
    set wid to missing value
    repeat with w in windows
      repeat with i from 1 to count of tabs of w
        if tty of tab i of w is target then
          set wid to id of w
          set tabIndex to i
          exit repeat
        end if
      end repeat
      if wid is not missing value then exit repeat
    end repeat
    if wid is missing value then return "no"
    set miniaturized of window id wid to false
    set selected of tab tabIndex of window id wid to true
    set index of window id wid to 1
    activate
  end tell
  return "yes"
end run'''


def raise_tab(tty: str) -> bool:
    """Bring the Terminal tab on /dev/<tty> to the front (cjump's AppleScript)."""
    return _osascript(_RAISE, f"/dev/{tty}") == "yes"


_NEW_TAB = '''
on run argv
  tell application "Terminal"
    activate
    do script (item 1 of argv)
  end tell
end run'''


def resume_in_new_tab(cwd: Path, session_id: str, text: str) -> None:
    cmd = " ".join(["cd", shlex.quote(str(cwd)), "&&", "claude", "--resume", session_id,
                    *config.RESUME_FLAGS, shlex.quote(text)])
    _osascript(_NEW_TAB, cmd)
