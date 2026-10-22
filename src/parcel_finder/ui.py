"""Notifications and the session pick-list (osascript)."""
import json
import subprocess


def notify(title: str, text: str) -> None:
    subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title {json.dumps(title)}"],
                   capture_output=True)
