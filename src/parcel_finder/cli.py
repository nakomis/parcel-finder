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

    register_callback()
    srv = make_server(handle)
    print(f"parcel-finder listening on http://{config.HOST}:{config.PORT}", flush=True)
    srv.serve_forever()
    return 0


def register_callback() -> None:
    from .orders.aliexpress import AliExpress, Reauthorise
    from .service import register_route

    def callback(query):
        code = query.get("code", [None])[0]
        if not code:
            return 400, "No ?code= in the callback (is passthrough set on the nakom.is link?)."
        try:
            AliExpress.from_keychain().complete(code)
        except Reauthorise as e:
            return 502, f"Token swap failed: {e}"
        return 200, "parcel-finder: AliExpress authorised, you can close this tab."

    register_route("/aliexpress/callback", callback)


def authorise(args) -> int:
    from .orders.aliexpress import AliExpress
    ae = AliExpress.from_keychain()
    if args.code:
        ae.complete(args.code)
        print("authorised")
        return 0
    subprocess.run(["open", ae.authorise_url()])
    print("Approve the app in the browser; the running service catches the callback.")
    return 0


def refresh(args) -> int:
    from .orders.aliexpress import AliExpress, Reauthorise
    from . import ui
    try:
        AliExpress.from_keychain().refresh()
    except Reauthorise as e:
        ui.notify("parcel-finder", "AliExpress needs re-authorising: run `parcel-finder authorise`")
        print(f"reauthorise needed: {e}")
        return 1
    print("refreshed")
    return sync_orders(args)


def collect_orders(sync_if_stale: bool = True) -> tuple[list, str | None]:
    """All recent orders from the cache (synced from Gmail if stale) plus AliExpress details.
    AliExpress problems are reported, never fatal."""
    from .orders import cache
    from .orders.aliexpress import AliExpress, ApiError, Reauthorise
    c = cache.OrderCache()
    if sync_if_stale and c.stale():
        cache.sync(c)
    found = list(c.orders)
    problem = None
    try:
        ae = AliExpress.from_keychain().orders(list(c.ae_ids))
        for o in ae:
            o.thumbnail_urls = c.ae_ids.get(o.order_id, [])
        found += ae
    except (Reauthorise, ApiError, config.MissingSecret) as e:
        problem = str(e)
    return found, problem


def sync_orders(args) -> int:
    from .orders import cache
    cache.sync(cache.OrderCache())
    print("orders synced")
    return 0


def orders(args) -> int:
    from .orders.aliexpress import AliExpress
    if not args.ids:
        found, problem = collect_orders()
        for o in found:
            print(f"{o.shop} {o.order_id or '-'} {o.ordered_at:%Y-%m-%d} {o.status}: {o.title}")
        if problem:
            print(f"AliExpress unavailable: {problem}")
        return 0
    for o in AliExpress.from_keychain().orders(args.ids):
        print(f"{o.shop} {o.order_id} {o.ordered_at:%Y-%m-%d} {o.status}: {o.title}")
    return 0


def find_sessions(args) -> int:
    from . import sessions
    from .identify import Identification
    cands = sessions.search(args.query, None)
    for c in cands[:5]:
        print(f"{c.score:5.1f} {c.session_id} {c.title} ({c.hits} hits, last {c.when:%d %b})")
    ch = sessions.choose(Identification(args.query, "medium", "", "cli", None), None, cands)
    print(f"choice: {ch.chosen.title if ch.chosen else None} clear={ch.clear}: {ch.reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="parcel-finder")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the loopback service").set_defaults(fn=serve)
    a = sub.add_parser("authorise", help="authorise the AliExpress app on your account")
    a.add_argument("--code", help="paste the ?code= by hand")
    a.set_defaults(fn=authorise)
    sub.add_parser("refresh", help="refresh the AliExpress tokens and sync order emails").set_defaults(fn=refresh)
    sub.add_parser("sync", help="sync order emails from Gmail into the cache").set_defaults(fn=sync_orders)
    o = sub.add_parser("orders", help="list orders")
    o.add_argument("ids", nargs="*", help="AliExpress order IDs")
    o.set_defaults(fn=orders)
    f = sub.add_parser("sessions", help="find the session for a description")
    f.add_argument("query")
    f.set_defaults(fn=find_sessions)
    d = sub.add_parser("doctor", help="check everything the finder needs")
    d.add_argument("--skip", action="append", help="name of a check to skip")
    d.set_defaults(fn=doctor)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
