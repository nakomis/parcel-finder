"""Constants from the design, and keychain access."""
import subprocess
from pathlib import Path

HOME = Path.home()

HAIKU_MODEL = "claude-haiku-5-5"
OLLAMA_URL = "http://localhost:11434"
OCR_MODEL = "glm-ocr"
OCR_PROMPT = "Text Recognition:"

HOST = "127.0.0.1"
PORT = 8765

SHARE_ROOT = Path("/Volumes/share/parcel-finder")
LOG_DIR = HOME / "Library/Logs/parcel-finder"
SCREENEYE_DIR = HOME / "Pictures/ScreenEye"  # the only place /photo accepts pictures from
SESSIONS_DIR = HOME / ".claude/sessions"
PROJECTS_DIR = HOME / ".claude/projects"
META_MCP_CONFIG = HOME / "repos/nakomis/mcps/meta-mcp/config.toml"
CONV_MEM_PROJECT = HOME / "repos/nakomis/claude-chats/mcp"

ORDER_WINDOW_DAYS = 60

ANTHROPIC_KEYCHAIN = ("parcel-finder", "anthropic-api-key")
AE_SERVICE = "aliexpress-open-platform"
AE_GATEWAY = "https://api-sg.aliexpress.com/sync"
AE_REST = "https://api-sg.aliexpress.com/rest"
AE_AUTHORISE = "https://api-sg.aliexpress.com/oauth/authorize"
AE_REDIRECT = "https://nakom.is/alifinder"

RESUME_FLAGS = ["--allow-dangerously-skip-permissions"]


class MissingSecret(Exception):
    def __init__(self, service: str, account: str):
        super().__init__(f"no keychain item for service {service!r}, account {account!r}")
        self.service, self.account = service, account


def secret(service: str, account: str) -> str:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise MissingSecret(service, account)
    return r.stdout.strip()


def store_secret(service: str, account: str, value: str) -> None:
    try:
        subprocess.run(["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", value],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError):
        # The exception would carry argv, and so the secret, into logs and notifications.
        raise RuntimeError(f"could not store keychain item {service}/{account}") from None
