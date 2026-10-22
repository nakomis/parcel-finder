"""Command line: serve | run | replay | authorise | refresh | doctor."""
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request

from . import config


def _check_ollama() -> str:
    with urllib.request.urlopen(f"{config.OLLAMA_URL}/api/tags", timeout=5) as r:
        names = [m["name"] for m in json.load(r)["models"]]
    if not any(n.split(":")[0] == config.OCR_MODEL for n in names):
        raise RuntimeError(f"{config.OCR_MODEL} not pulled")
    return f"{config.OCR_MODEL} available"


def _check_secrets() -> str:
    missing = []
    for service, account in [config.ANTHROPIC_KEYCHAIN] + [(config.AE_SERVICE, a) for a in
                                                         ("app-key", "app-secret", "access-token", "refresh-token")]:
        try:
            config.secret(service, account)
        except config.MissingSecret:
            missing.append(f"{service}/{account}")
    if missing:
        raise RuntimeError("missing: " + ", ".join(missing))
    return "all present"


def _check_share() -> str:
    config.SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=config.SHARE_ROOT):
        pass
    return f"{config.SHARE_ROOT} writable"


def _check_terminal() -> str:
    r = subprocess.run(["osascript", "-e", 'tell application "Terminal" to count windows'],
                       capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return f"{r.stdout.strip()} Terminal windows"


def _check_gmail() -> str:
    r = subprocess.run(["claude", "-p", "--model", "haiku", "--no-session-persistence", "--output-format", "text",
                        "--", "List the names of your tools containing 'Gmail', one per line. Do not call them."],
                       capture_output=True, text=True, timeout=180, cwd=tempfile.gettempdir(),
                       stdin=subprocess.DEVNULL)
    if "mcp__claude_ai_Gmail__" not in r.stdout:
        raise RuntimeError("Gmail connector not visible to claude -p")
    return "Gmail connector visible"


def _check_sessions() -> str:
    return f"{len(list(config.SESSIONS_DIR.glob('*.json')))} live session files"


CHECKS = [("Ollama OCR", _check_ollama), ("keychain", _check_secrets), ("share", _check_share),
          ("Terminal automation", _check_terminal), ("claude -p Gmail", _check_gmail),
          ("sessions dir", _check_sessions)]


def doctor(args) -> int:
    failed = 0
    for name, check in CHECKS:
        if args.skip and name in args.skip:
            print(f"- {name}: skipped")
            continue
        try:
            print(f"✓ {name}: {check()}")
        except Exception as e:  # report every check, whatever breaks
            failed += 1
            print(f"✗ {name}: {e}")
    return 1 if failed else 0


def serve(args) -> int:
    from .runlog import RunLog
    from .service import make_server

    def handle(photo):
        # Replaced by pipeline.run in Task 8.
        RunLog.start(photo).finish("received")

    srv = make_server(handle)
    print(f"parcel-finder listening on http://{config.HOST}:{config.PORT}", flush=True)
    srv.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="parcel-finder")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the loopback service").set_defaults(fn=serve)
    d = sub.add_parser("doctor", help="check everything the finder needs")
    d.add_argument("--skip", action="append", help="name of a check to skip")
    d.set_defaults(fn=doctor)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
