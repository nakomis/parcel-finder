"""One structured log per run: runs.jsonl (machine) and finder.log (human)."""
import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from . import config

_SECRET_KEY = re.compile(r"token|secret|key|code|authorization", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "<redacted>" if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class RunLog:
    def __init__(self, run_id: str, log_dir: Path):
        self.run_id, self.log_dir = run_id, log_dir

    @classmethod
    def start(cls, photo: Path, log_dir: Path = config.LOG_DIR) -> "RunLog":
        log_dir.mkdir(parents=True, exist_ok=True)
        log = cls(f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}", log_dir)
        log.event("start", photo=str(photo))
        return log

    def event(self, stage: str, **data) -> None:
        record = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                  "run_id": self.run_id, "stage": stage, **redact(data)}
        with open(self.log_dir / "runs.jsonl", "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def finish(self, outcome: str, **data) -> None:
        self.event("finish", outcome=outcome, **data)
        detail = " ".join(f"{k}={v}" for k, v in redact(data).items())
        with open(self.log_dir / "finder.log", "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {self.run_id} {outcome} {detail}".rstrip() + "\n")
