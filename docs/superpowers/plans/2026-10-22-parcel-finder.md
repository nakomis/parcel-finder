# Parcel finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Mac service that turns a ScreenEye photo of a delivered item into a message in the Claude Code session where the item was bought.

**Architecture:** A Python package (`parcel_finder`) run as a per-user LaunchAgent with a loopback HTTP server on port 8765. Each photo runs one serialised pipeline: identify (GLM-OCR, rules, Haiku) → recent orders (Gmail via `claude -p`, AliExpress Open Platform API) → match → find session (conversation-memory + Haiku) → deliver (photo to share, raise/resume, socket message). Every stage writes to one structured run log.

**Tech Stack:** Python 3.11+ managed with `uv`; `anthropic` SDK; stdlib `http.server`/`urllib`; `pytest`; macOS `osascript`, `security`, `launchctl`; Swift for the ScreenEye button.

**Spec:** `docs/superpowers/specs/2026-10-22-parcel-finder-design.md` (read it first; this plan argues from it).

**Plane:** HOME, module "Parcel finder". Each task names its item; branch `home-<n>-<slug>`, PR title `… (HOME-<n>)`.

## Global Constraints

- Model: `claude-haiku-5-5` via the Claude API (Bedrock is not entitled). Use the `claude-api` skill for SDK usage; no `temperature`/`top_p` (rejected since 2026-10-21); check `stop_reason == "refusal"` before reading content.
- Anthropic key: keychain service `parcel-finder`, account `anthropic-api-key` (created by `parcel-finder doctor --init-secrets` from the existing `cabal-mcp`/`anthropic-api-key` only if Martin confirms; never logged).
- AliExpress: keychain service `aliexpress-open-platform`, accounts `app-key`, `app-secret`, `access-token`, `refresh-token`. Gateway `https://api-sg.aliexpress.com/sync`; token endpoints under `https://api-sg.aliexpress.com/rest` with the API path prefixed to the signing string.
- OCR: Ollama `http://localhost:11434`, model `glm-ocr`, prompt `Text Recognition:`.
- Service: `127.0.0.1:8765` only. Routes: `POST /photo`, `GET /aliexpress/callback`, `GET /health`.
- Photos: `/Volumes/share/parcel-finder/YYYY-MM-DD/HHMMSS-<slug>.jpg`.
- Logs: `~/Library/Logs/parcel-finder/runs.jsonl` (one JSON object per event) and `finder.log` (human-readable). Never log values whose key contains `token`, `secret`, `key`, `code` or `authorization` (case-insensitive): write `"<redacted>"`.
- Order window: 60 days.
- Message text, exactly: `This just arrived! It's been identified as a {item}. What do I do with it? If you don't recognise it, please let me know!` followed by a blank line and `Photo: {photo_path}`.
- Resume command: `claude --resume <id> --allow-dangerously-skip-permissions "<message>"` in a new Terminal.app tab in the session's `cwd`.
- Auto-proceed only when there is an order match **and** one clear session; otherwise pick-list. A session whose transcript isn't on this Mac: notify "that session is on <host>" and stop.
- British English in all user-facing text. Tests run with `TEST_MAX_WORKERS=2`-style caps where relevant (`pytest -p no:xdist`).

## Review Focus

1. **Two presses in quick succession** → runs are serialised; the second waits and both complete (Task 2 test `test_runs_are_serialised`).
2. **`/Volumes/share` not mounted** → the photo stays at its local ScreenEye path, the message uses that path, and the notification says so (Task 7 test `test_copy_photo_falls_back_when_share_missing`).
3. **AliExpress token lapsed (away more than 48 h)** → orders still come from email; one notification "AliExpress needs re-authorising: run `parcel-finder authorise`"; the run carries on (Task 4 test `test_expired_refresh_token_raises_reauthorise`, Task 8 test `test_pipeline_continues_without_aliexpress`).
4. **Ollama down or `glm-ocr` missing** → OCR is skipped, Haiku gets the image only, the run log records why (Task 3 test `test_identify_without_ocr`).
5. **Nothing found** (no order match, no search hits) → notification "Couldn't find the session for this; see run <id>", and no message is sent anywhere (Task 8 test `test_nothing_found_sends_nothing`).

---

## File Structure

```
pyproject.toml                       uv project, console script `parcel-finder = parcel_finder.cli:main`
src/parcel_finder/
  config.py                          constants above; `secret(service, account) -> str`
  runlog.py                          RunLog (structured, redacting)
  haiku.py                           `ask_json(...)`: one Haiku call returning parsed JSON
  identify.py                        OCR, rules, Haiku identification
  orders/model.py                    Order, OrderMatch dataclasses
  orders/aliexpress.py               signing, tokens, order lookup
  orders/gmail.py                    order emails via `claude -p`
  orders/match.py                    which order is this photo?
  sessions.py                        conversation-memory search and choice
  delivery.py                        photo copy, live-session lookup, socket send, raise/resume
  ui.py                              notifications and pick-list (osascript)
  pipeline.py                        orchestration
  service.py                         HTTP server + serial worker
  cli.py                             serve | run | replay | authorise | refresh | doctor
launchd/com.nakomis.parcel-finder.plist          the service (RunAtLoad, KeepAlive)
launchd/com.nakomis.parcel-finder.refresh.plist  `parcel-finder refresh` every 12 h
scripts/install.sh                   copy plists to ~/Library/LaunchAgents, load them
tests/…                              one test file per module; fixtures under tests/fixtures/
tests/eval/cases.example.json        eval format; real cases.json (local photo paths) is gitignored
```

---

### Task 1: Project skeleton, config, run log, doctor (HOME-530)

**Files:**
- Create: `pyproject.toml`, `src/parcel_finder/__init__.py`, `config.py`, `runlog.py`, `cli.py`
- Test: `tests/test_runlog.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.secret(service: str, account: str) -> str` (runs `security find-generic-password -s … -a … -w`; raises `config.MissingSecret(service, account)`).
  - `RunLog.start(photo: Path, log_dir: Path = config.LOG_DIR) -> RunLog` with `.run_id: str` (`YYYYMMDD-HHMMSS-<4 hex>`), `.event(stage: str, **data) -> None`, `.finish(outcome: str, **data) -> None`.
  - `cli.main(argv: list[str] | None = None) -> int`, with subcommand `doctor`.

- [ ] **Step 1: Write the failing tests**

```python
def test_event_redacts_secret_like_keys(tmp_path):
    log = RunLog.start(Path("/p.jpg"), log_dir=tmp_path)
    log.event("orders", access_token="abc", nested={"Code": "x", "title": "Buzzer"})
    line = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert line["access_token"] == "<redacted>"
    assert line["nested"] == {"Code": "<redacted>", "title": "Buzzer"}
    assert line["run_id"] == log.run_id and line["stage"] == "orders"

def test_finish_writes_human_line(tmp_path):
    log = RunLog.start(Path("/p.jpg"), log_dir=tmp_path)
    log.finish("delivered", session="ddr-banging")
    assert f"{log.run_id} delivered" in (tmp_path / "finder.log").read_text()

def test_secret_missing_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 44, "", ""))
    with pytest.raises(MissingSecret):
        secret("nope", "nope")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_runlog.py tests/test_config.py -v`. Expected: FAIL (import errors).
- [ ] **Step 3: Implement** `config.py`, `runlog.py` and the `doctor` subcommand. `doctor` runs and prints ✓/✗ for each feasibility check from the spec's Packaging section: Ollama reachable with `glm-ocr`; keychain secrets present; `/Volumes/share` writable; `osascript -e 'tell application "Terminal" to count windows'`; `claude -p` lists a `mcp__claude_ai_Gmail__` tool (run without `--strict-mcp-config`); `~/.claude/sessions` readable. Exit 1 if any fail.
- [ ] **Step 4: Run** the tests again. Expected: PASS. Then run `uv run parcel-finder doctor` and paste its output into the PR.
- [ ] **Step 5: Commit** `feat: project skeleton, run log and doctor (HOME-530)`.

### Task 2: Service, serial worker, LaunchAgent (HOME-530)

**Files:**
- Create: `service.py`, `launchd/*.plist`, `scripts/install.sh`
- Modify: `cli.py` (add `serve`)
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `RunLog`.
- Produces: `make_server(handler: Callable[[Path], None], port: int = 8765) -> ThreadingHTTPServer`. `POST /photo` accepts JSON `{"path": "<abs path to jpg>"}` or a multipart `image` field (saved under `~/Pictures/ScreenEye/parcel-finder/`) and replies `202 {"queued": true, "path": …}`. One worker thread calls `handler` for each photo in order. `GET /health` returns `200 {"ok": true, "queue": n}`. Task 4 adds `GET /aliexpress/callback` via `register_route(path, fn)`.

- [ ] **Step 1: Write the failing tests:** `test_post_path_enqueues`, `test_rejects_non_loopback_bind` (`make_server` refuses any host other than `127.0.0.1`), `test_runs_are_serialised` (two POSTs; the handler records start and end times; assert the second start ≥ the first end), and `test_missing_file_is_400`.
- [ ] **Step 2: Run** `uv run pytest tests/test_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `http.server.ThreadingHTTPServer` plus a `queue.Queue` and a single worker thread. The plists run `uv run --project ~/repos/nakomis/parcel-finder parcel-finder serve` (service, `KeepAlive` true) and `… refresh` (`StartInterval` 43200). `install.sh` copies the plists to `~/Library/LaunchAgents` and runs `launchctl bootstrap gui/$(id -u)`.
- [ ] **Step 4: Run** the tests. Expected: PASS. Then run `scripts/install.sh`, `curl -s 127.0.0.1:8765/health`, and run `doctor` *via the agent* (`launchctl kickstart`, with output in `finder.log`). Approve the macOS prompts. If a check fails only under launchd, stop and report: the spec's fallback is to host inside ScreenEye.
- [ ] **Step 5: Commit** `feat: loopback service and LaunchAgent (HOME-530)`.

### Task 3: Identification (HOME-531)

**Files:**
- Create: `haiku.py`, `identify.py`, `tests/eval/cases.example.json`, `tests/eval/run_eval.py`
- Test: `tests/test_identify.py`, `tests/test_haiku.py`

**Interfaces:**
- Produces:
  - `haiku.ask_json(prompt: str, images: list[bytes], schema: dict, max_tokens: int = 2000) -> dict` (JPEG bytes; structured outputs per the `claude-api` skill; raises `haiku.Refused` on refusal).
  - `identify.ocr(photo: Path) -> str | None` (`None` if Ollama or the model is unavailable).
  - `identify.tokens(text: str) -> set[str]`.
  - `identify.match_rule(toks: set[str]) -> str | None`.
  - `identify.is_informative(text: str | None) -> bool`.
  - `identify.identify(photo: Path, log: RunLog) -> Identification`, where `@dataclass Identification(item: str, confidence: str, evidence: str, source: str, ocr_text: str | None)` and `source` ∈ `{"rule", "haiku", "haiku-image-only"}`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_tokens_fold_ocr_typos():
    assert {"CMD", "SI", "D0", "D1", "DAT2"} <= tokens("CLK D0 GMD D3 D1\nST\nDAT2")

def test_rule_sdio_breakout():
    assert match_rule(tokens("3V\nCLK D0 GMD D3 D1\nDET\nGND\nSO\nST\nCS\nDAT2")) == "microSD breakout, SPI + 4-bit SDIO"

def test_rule_spi_only():
    assert match_rule({"CS", "MISO", "MOSI", "SCK", "GND", "VCC"}) == "microSD breakout, SPI only"

def test_generic_words_are_not_informative():
    assert not is_informative("PWR\nRESET\nBOOT")
    assert is_informative("3V\nCLK D0 GMD D3 D1\nDET\nGND\nSO\nST\nCS\nDAT2")

def test_identify_without_ocr(monkeypatch, tmp_path):   # Review Focus 4
    monkeypatch.setattr(identify, "ocr", lambda p: None)
    monkeypatch.setattr(identify.haiku, "ask_json", lambda prompt, images, schema, **k: {"item": "USB tester", "confidence": "medium", "evidence": "labels"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku-image-only" and r.ocr_text is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_identify.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The rules and folding come from the spike's `pipeline.py` (in the spec's history). `is_informative`: at least four distinct tokens, at least one of which is not in `{"PWR","RESET","BOOT","GND","VCC","3V","5V","3V3"}`. Pass OCR text to Haiku only when it is informative. The Haiku schema is `{item, confidence: high|medium|low, evidence}`.
- [ ] **Step 4: Run** the tests. Expected: PASS. Then `uv run python tests/eval/run_eval.py tests/eval/cases.json` against the three local photos. Expected: the 4682 clone via `rule`; the USB tester and the Waveshare pair via Haiku, with the Waveshare case **not** claiming an Ethernet chip. Record the output in the PR.
- [ ] **Step 5: Commit** `feat: identification pipeline and eval (HOME-531)`.

### Task 4: AliExpress client and tokens (HOME-532)

**Files:**
- Create: `orders/__init__.py`, `orders/model.py`, `orders/aliexpress.py`, `tests/fixtures/ae_order.json` (from the spike response, with IDs and amounts changed)
- Modify: `cli.py` (`authorise`, `refresh`), `service.py` (register `/aliexpress/callback`)
- Test: `tests/test_aliexpress.py`

**Interfaces:**
- Produces:
  - `@dataclass Order(shop: str, order_id: str, title: str, ordered_at: datetime, status: str, product_id: str | None = None, thumbnail_urls: list[str] = field(default_factory=list))`. One `Order` per non-cancelled line item.
  - `aliexpress.sign(params: dict[str, str], secret: str, api_path: str = "") -> str`.
  - `class AliExpress(key, secret, store=KeychainTokenStore())` with:
    - `.orders(order_ids: list[str]) -> list[Order]`
    - `.refresh() -> None`
    - `.authorise_url() -> str`
    - `.complete(code: str) -> None`
    - all raising `aliexpress.Reauthorise` when the refresh token has lapsed.

- [ ] **Step 1: Write the failing tests:**

```python
def test_sign_business_call():
    p = {"app_key": "12345", "timestamp": "1700000000000", "sign_method": "sha256", "method": "aliexpress.ds.category.get"}
    assert sign(p, "testsecret") == "79BCF4A71F7E5BCA5E3D9A5F836BFFC7EFD7BF26EA98346E6C9DB777D77447F1"

def test_sign_rest_call_prefixes_path():
    p = {"app_key": "12345", "timestamp": "1700000000000", "sign_method": "sha256", "code": "abc"}
    assert sign(p, "testsecret", "/auth/token/create") == "D3DDCDF3953B4DC26EFC38A6F89959B7D4F25D3542072143FF53D963A5BBC8B0"

def test_orders_skip_cancelled_lines(fake_http):   # fixture has one live and one CANCELED line
    titles = [o.title for o in AliExpress("k", "s", store=MemoryStore(valid=True)).orders(["1"])]
    assert titles == ["Estardyn Active/Passive Dual Mode Buzzer Module 5V, High/Low Level Trigger & PWM for Raspberry Pi ESP32"]

def test_expired_refresh_token_raises_reauthorise():   # Review Focus 3
    with pytest.raises(Reauthorise):
        AliExpress("k", "s", store=MemoryStore(refresh_expired=True)).orders(["1"])
```

- [ ] **Step 2: Run** `uv run pytest tests/test_aliexpress.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Store the tokens' expiry times (`expire_time`, `refresh_token_valid_time`, both epoch ms) alongside them in the keychain, as JSON under account `token-meta`. Refresh via `/auth/token/refresh` when the access token is within 2 h of expiry. `authorise` opens `authorise_url()` (redirect `https://nakom.is/alifinder`, `force_auth=true`), and the service's `/aliexpress/callback` calls `complete(code)` and replies "authorised, you can close this tab". Until HOME-529 is deployed with passthrough set on `alifinder`, `authorise --code <code>` is the manual path.
- [ ] **Step 4: Run** the tests. Expected: PASS. Then `uv run parcel-finder refresh` (succeeds while the 22 Oct token is still valid) and `uv run parcel-finder run --orders-only 3076931683840500`. Expected: the buzzer title printed.
- [ ] **Step 5: Commit** `feat: AliExpress order client with token refresh (HOME-532)`.

### Task 5: Order emails and order matching (HOME-532)

**Files:**
- Create: `orders/gmail.py`, `orders/match.py`
- Test: `tests/test_gmail.py`, `tests/test_match.py`

**Interfaces:**
- Consumes: `Order`, `AliExpress.orders`, `haiku.ask_json`.
- Produces:
  - `gmail.recent_orders(days: int = 60) -> tuple[list[Order], list[str]]`: Amazon and eBay `Order`s from subjects, plus AliExpress order IDs. It runs `claude -p --model haiku --output-format json --json-schema <schema>` with a prompt to search `category:purchases newer_than:<days>d` and return `{shop, order_id, title, ordered_at, status, thumbnail_urls}`. Thumbnails are AliExpress product images from the email HTML, when present.
  - `match.match_order(photo: Path, ident: Identification, orders: list[Order]) -> OrderMatch | None`, where `@dataclass OrderMatch(order: Order, confidence: str, reason: str)`. It sends Haiku the photo plus up to 20 candidate titles and up to 8 thumbnails, asks "which of these, or none". Delivered more than 14 days ago counts as an unlikely candidate.

- [ ] **Step 1: Write the failing tests:** `test_recent_orders_parses_claude_json` (a fake `subprocess.run` returns a recorded `structured_output`); `test_recent_orders_never_uses_strict_mcp_config` (the argv lacks `--strict-mcp-config` and `--tools`); `test_match_returns_none_when_haiku_says_none`; `test_match_drops_long_delivered_orders` (an order delivered 30 days ago isn't offered to Haiku).
- [ ] **Step 2: Run** `uv run pytest tests/test_gmail.py tests/test_match.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS. Live check: `uv run parcel-finder run --orders-only` lists this month's Amazon, eBay and AliExpress orders.
- [ ] **Step 5: Commit** `feat: recent orders from Gmail and order matching (HOME-532)`.

### Task 6: Finding the session (HOME-533)

**Files:**
- Create: `sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `Identification`, `OrderMatch`.
- Produces:
  - `@dataclass Candidate(session_id: str, title: str, project: str, when: datetime, hits: int, snippet: str)`.
  - `@dataclass SessionChoice(chosen: Candidate | None, clear: bool, candidates: list[Candidate], reason: str)`.
  - `sessions.search(query: str, around: datetime | None) -> list[Candidate]`: imports `conversation_memory_mcp.server.search_memory` exactly as `~/scripts/cjump` does (env from `meta-mcp/config.toml`, `since="all"`, limit 20, grouped by session). When `around` is set, sessions with messages in the 72 h before `around` get a boost.
  - `sessions.choose(ident, match, candidates) -> SessionChoice`: Haiku via `claude -p --json-schema` with the purchase-focused instruction from the spec. `clear` is True only when `match` is set, Haiku picks with high confidence, and no other candidate scored within 25 % of the winner.
  - `sessions.local_transcript(session_id: str) -> Path | None` (`~/.claude/projects/*/<id>.jsonl`).
  - `sessions.host_of(session_id) -> str | None` (from conversation-memory's project path or host field, for the "it's on mu" message).

- [ ] **Step 1: Write the failing tests:** `test_time_weighting_prefers_sessions_before_order`; `test_choice_not_clear_without_order_match`; `test_choice_not_clear_when_runner_up_close`; `test_local_transcript_missing_returns_none`.
- [ ] **Step 2: Run** `uv run pytest tests/test_sessions.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS. Live check: `uv run parcel-finder run --session-only "microSD SDIO breakout Adafruit 4682"` puts ddr-banging (`0aedf57a-…`) first.
- [ ] **Step 5: Commit** `feat: purchasing-session search and choice (HOME-533)`.

### Task 7: Delivery and UI (HOME-534)

**Files:**
- Create: `delivery.py`, `ui.py`
- Test: `tests/test_delivery.py`

**Interfaces:**
- Produces:
  - `delivery.copy_photo(photo: Path, slug: str, now: datetime) -> tuple[Path, bool]`: the destination and whether it went to the share.
  - `delivery.message(item: str, photo_path: Path) -> str`: the exact text from Global Constraints.
  - `@dataclass LiveSession(pid: int, socket: Path, key_file: Path, tty: str | None, cwd: Path, status: str)`.
  - `delivery.live_session(session_id: str, sessions_dir: Path = ~/.claude/sessions) -> LiveSession | None` (checks that the pid is alive).
  - `delivery.send(live: LiveSession, text: str) -> None`: two JSON lines, auth then user, as in midichlorian's `notify.py`.
  - `delivery.raise_tab(tty: str) -> bool` (cjump's AppleScript).
  - `delivery.resume_in_new_tab(cwd: Path, session_id: str, text: str) -> None`.
  - `ui.notify(title: str, text: str) -> None`.
  - `ui.choose(candidates: list[Candidate]) -> str | None` (AppleScript `choose from list`; returns a session ID or None).

- [ ] **Step 1: Write the failing tests:**

```python
def test_message_text_exact(tmp_path):
    assert message("USB cable tester", Path("/v/p.jpg")) == (
        "This just arrived! It's been identified as a USB cable tester. What do I do with it? "
        "If you don't recognise it, please let me know!\n\nPhoto: /v/p.jpg")

def test_copy_photo_falls_back_when_share_missing(tmp_path, monkeypatch):   # Review Focus 2
    monkeypatch.setattr(delivery, "SHARE_ROOT", tmp_path / "not-mounted")
    dest, shared = copy_photo(photo_fixture, "usb-tester", datetime(2026, 10, 22, 17, 30))
    assert not shared and dest == photo_fixture

def test_send_writes_auth_then_user(unix_socket_server, tmp_path): ...     # reads the two lines, asserts types and token from key file
def test_live_session_ignores_dead_pid(tmp_path): ...                     # session json with a pid that isn't running → None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_delivery.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Escape the message for the shell with `shlex.quote` inside the AppleScript `do script`. A busy session (`status: "busy"`) still gets the message straight away: Claude Code queues socket messages until the session's next tool round (observed on 22 Oct), so no waiting is needed.
- [ ] **Step 4: Run** the tests. Expected: PASS. Live check: send a test message to a throwaway session started for the purpose (never to a real working session), and check it arrives.
- [ ] **Step 5: Commit** `feat: delivery to the session, notifications and pick-list (HOME-534)`.

### Task 8: Pipeline, replay, end to end (HOME-530 to HOME-534)

**Files:**
- Create: `pipeline.py`
- Modify: `cli.py` (`run <photo>`, `run --orders-only`, `run --session-only <query>`, `replay <run-id>`), `service.py` (use `pipeline.run` as the handler)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `pipeline.run(photo: Path, deps: Deps = Deps.real()) -> Outcome`, where `Deps` bundles the stage functions so tests inject fakes, and `@dataclass Outcome(status: str, run_id: str, session_id: str | None, detail: str)` with `status` ∈ `{"delivered", "resumed", "picked", "cancelled", "elsewhere", "not-found", "error"}`.

Flow: identify → orders (AliExpress failures logged and notified once, never fatal) → match → search (the query is the order title if matched, else `ident.item`) → choose → if not clear, `ui.choose` → if no local transcript, notify "that session is on <host>" and stop → copy photo → live? `raise_tab` + `send` : `resume_in_new_tab` → notify "Identified: <item> → <session title>".

- [ ] **Step 1: Write the failing tests:** `test_clear_match_delivers_without_asking`; `test_unclear_match_asks_and_respects_cancel`; `test_session_elsewhere_stops`; `test_pipeline_continues_without_aliexpress` (Review Focus 3); `test_nothing_found_sends_nothing` (Review Focus 5); `test_every_stage_logged` (one `runs.jsonl` event per stage, sharing the run_id).
- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `pipeline.run`. `replay <run-id>` reads that run's photo path from `runs.jsonl` and re-runs it with `--dry-run` (no delivery).
- [ ] **Step 4: Run** `uv run pytest -v` (whole suite) and `uv run pytest --cov=parcel_finder` (≥ 70 %). Then do an end-to-end check with Martin: `uv run parcel-finder run /tmp/IMG_0194.jpg --dry-run`. Expected: the order or search finds ddr-banging, and the log shows each stage.
- [ ] **Step 5: Commit** `feat: end-to-end pipeline and replay (HOME-530…534)`.

### Task 9: ScreenEye "Open in Claude" button (HOME-535, repo `nakomis/mcps`)

**Files:**
- Modify: `screeneye/macos/ScreenEyeMac/` (the view holding Take Picture, and the picture-saving code)
- Create: `screeneye/macos/ScreenEyeMac/ParcelFinderClient.swift`
- Test: `screeneye/macos/ScreenEyeMacTests/ParcelFinderClientTests.swift`

**Interfaces:**
- Consumes: the service's `POST /photo` with JSON `{"path": …}` (Task 2).
- Produces: `struct ParcelFinderClient { func request(for savedPath: URL) -> URLRequest; func send(savedPath: URL) async throws }`, targeting `http://127.0.0.1:8765/photo`.

- [ ] **Step 1: Write the failing test** `testRequestPostsJSONPathToLoopback`: the method is POST, the URL is `http://127.0.0.1:8765/photo`, and the body decodes to `["path": savedPath.path]`.
- [ ] **Step 2: Run** `xcodebuild -project ScreenEyeMac.xcodeproj -scheme ScreenEyeMac -destination 'platform=macOS' test`. Expected: FAIL.
- [ ] **Step 3: Implement** the client, and an **Open in Claude** button (⌘⇧O) beside Take Picture. It takes the picture exactly as Take Picture does (same save path and sidecar, note `parcel`), then calls `send`. On a connection failure it shows "parcel-finder isn't running".
- [ ] **Step 4: Run** the tests. Expected: PASS. Live: press the button with the service running, and a run appears in `finder.log`.
- [ ] **Step 5: Commit** in the mcps repo on branch `home-535-open-in-claude`: `feat(screeneye): Open in Claude button for parcel-finder (HOME-535)`.

---

## Outside this plan

- **HOME-529** (nakom.is per-link passthrough, `nis --passthrough`) is being done separately. Task 4's automatic callback depends on it; the manual `--code` path covers the gap.
- Sonnet escalation, the phone-app button and a UI are deliberately left out (spec non-goals and "measure first").
