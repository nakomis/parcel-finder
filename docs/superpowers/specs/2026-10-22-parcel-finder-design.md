# Parcel finder: from a photo of a delivered item to the Claude session that bought it

Date: 22 Oct 2026. Status: draft for Martin's review.

## Intent

Martin buys a lot of parts (mostly AliExpress, also Amazon and eBay) during Claude Code
sessions: a session researches a part, often picks it, sometimes even fills the basket via
Claude-in-Chrome. Weeks later a parcel arrives and the context of *why* it was bought lives
in that old session. The finder closes the loop: Martin, sat at his Mac, holds the item up
to the ScreenEye phone camera, presses **Open in Claude**, and the right Claude session is
brought to the front (or resumed) with the photo and the message:

> This just arrived! It's been identified as a <XXX>. What do I do with it? If you don't
> recognise it, please let me know!

**The finder is only the link from a photo to a session.** Once the message is delivered,
the receiving session takes over; the finder never tries to answer "what do I do with it".

## Goals

1. One button press in the ScreenEye Mac app starts the whole flow.
2. Identify the item well enough to find the purchasing session.
3. Find the session where Martin decided to buy it (not merely one that mentions it).
4. Make the photo readable to that session, then raise it if open or resume it if closed,
   and deliver the message above.
5. Thorough logging of every run, so Martin (or Claude) can work out what went wrong.

## Non-goals

- No UI of its own for now: no window, no history view. Logs are the diagnostic surface.
- Not a general object recogniser; it only needs to get far enough to find the session.
- Not for photos taken on the phone away from the Mac (the phone app's button can come later).
- No acting on the item (no Plane items, no stock lists): the receiving session does that.

## Anti-goals (would make it a failure even if it "works")

- **Confidently waking the wrong session.** A session told about a parcel it never ordered
  wastes Martin's time and pollutes that session. When unsure, ask Martin (see Ambiguity).
- Interrupting a session mid-task in a way that derails it. (Delivery while busy: see open
  items for the builder.)
- Leaking credentials: Gmail, AliExpress and Anthropic secrets never written to logs.

## Constraints and facts established

**Machine and environment.** Runs on Martin's workstation, `phi` (macOS, 24 GB, Apple
Silicon). Ollama runs locally on phi (`http://localhost:11434`); `glm-ocr` and the other test
models are already pulled. Martin is at the Mac whenever he uses it.

**Capture: ScreenEye** (`~/repos/nakomis/mcps/screeneye`). The phone app streams its
camera; the Mac app (`screeneye/macos/`, SwiftUI, xcodegen) shows it live and "Take Picture"
(⌘S) saves the full-resolution upright JPEG to `~/Pictures/ScreenEye/YYYY-MM-DD/HHMMSS[-note].jpg`
with a `.json` sidecar (`note`, `taken`, camera status). **Change:** add a second button to
the Mac app, "Open in Claude", which takes the same picture and hands it to the finder.
ScreenEye stays a camera app that knows nothing about Claude; it just pokes the finder.

**The finder** is a small background service on phi, started at login (launchd), listening
on the loopback interface only. The ScreenEye button POSTs the picture (or its saved path)
to it. Everything else happens in the finder.

**Identification pipeline (proven on 22 Oct 2026 in a spike):**
- Stage A, local OCR: `glm-ocr` via Ollama, prompt `Text Recognition:`, ~4–8 s. It read
  board silkscreen near-perfectly where 4–12 B general VLMs failed.
- Stage B, rules: small hand-written signatures over the OCR tokens (with OCR-typo folding,
  e.g. GMD→CMD) for item kinds Martin cares about, e.g. microSD breakout SPI-only vs SDIO.
  First match wins; most items will not match a rule.
- Stage C, Claude Haiku 5.5 (`claude-haiku-5-5`) with the image. Pass the OCR text **only
  when it is informative** (pin names, part numbers, several words): on the Waveshare photo,
  three generic words (`PWR RESET BOOT`) primed Haiku to invent an Ethernet chip from a logo.
  ~5 s, ~2.6k input / ~0.6k output tokens, about $0.0006 a call.
- Ruled out: off-the-shelf PCB detectors (SanderGi/PCB-OBB, PCBInspect-AI, YOLO-World) for
  cropping; they boxed the background, a laptop screen, or the hand. Full-frame OCR worked.
  The spike script is `pipeline.py` (in the session scratchpad; to be copied into the repo).

**Recent orders as the strongest signal.** A parcel is almost always a recent order, so
"which of these orders is this?" is much easier than "what is this?". Order sources:
- **Amazon** (`*@amazon.co.uk`, to a per-shop alias) and **eBay** (`ebay@ebay.com`, to
  a per-shop alias): Ordered / Dispatched / Delivered emails carry a truncated product
  title in the subject, and an exact timestamp.
- **AliExpress** (`transaction@notice.aliexpress.com`): subjects and plain text carry only
  order or package numbers; products appear as **thumbnail images** in the HTML (whether alt
  text carries titles is unchecked). One checkout splits into many orders (19 Oct's basket
  became ten). Each email still gives the order time.
- **AliExpress Open Platform API: use it if it's there.** Martin applied on 22 Oct 2026 as
  *Dropshipping/联盟开发者 → Dropshipping (个人, individual)*; the developer profile was
  **approved the same afternoon** (email "Profile Approval Granted", 17:01). App created
  (category Drop Shipping, callback `https://nakom.is/alifinder` →
  `http://127.0.0.1:8775/aliexpress/callback`; moved from 8765 on 22 Oct, which the playlist backend also uses). Keychain service `aliexpress-open-platform`,
  accounts `app-key`, `app-secret`, `access-token`, `refresh-token`. The compliance form
  declares Anthropic (USA) as a third-party processor of product titles and images.
  **Spike results (22 Oct):**
  - Gateway `https://api-sg.aliexpress.com/sync` (business calls) and `/rest/auth/token/create`
    (token swap; the API path is prefixed to the signing string); HMAC-SHA256 over sorted
    `key+value` pairs, upper-case hex. Token-less calls (`aliexpress.ds.category.get`) work.
  - OAuth: `https://api-sg.aliexpress.com/oauth/authorize?response_type=code&force_auth=true&redirect_uri=…&client_id=…`.
    The token came back for Martin's account with `account_platform: buyerApp`.
  - **Access token lasts 24 h, refresh token 48 h.** The finder must refresh at least daily
    (a launchd timer), or Martin re-authorises every two days; re-authorisation must be a
    one-command flow.
  - **`nakom.is` drops query strings** on its 301, so `?code=` never reached the listener;
    the spike recovered it from Chrome's history. Fix the shortener to pass query strings
    through (separate work item). The nakom.is Lambda logs will contain codes.
  - `aliexpress.trade.ds.order.get` with `single_order_query={"order_id": …}` returns full
    **product titles per line item**, product and SKU IDs, store, prices, `gmt_create`,
    paid time, `order_status`, `logistics_status`, tracking numbers. Lines can carry
    `end_reason: CANCELED`: skip them. No order-*list* call found yet: **order IDs come from
    the AliExpress emails' subjects** ("Order <id>: order shipped"), details from the API.
  - Product images are not in the order data; `product_id` should fetch them via a
    product-detail call (untested).
  If the API is unavailable (token lapsed or revoked), fall back to email thumbnails plus
  order time. The design must not depend on it. Caution: the membership terms let AliExpress suspend the *buyer* account at
  its discretion, so the API is optional and read-only.
- Gmail access: in this environment Gmail is a claude.ai connector available to Claude
  sessions, not to a background service. **Decided (my call):** reach Gmail through a
  one-off headless `claude -p` call, to keep credentials out of the finder. **Verified
  22 Oct:** `claude -p` lists the `mcp__claude_ai_Gmail__*` tools when run *without*
  cjump's `--strict-mcp-config --tools ""` (those flags strip connectors, so the Gmail call
  must not use them). Still to confirm from the launchd context (see Packaging). Fallback
  only if that fails: own Gmail API OAuth, read-only scope.
- **One mailbox.** The Amazon and eBay emails (sent to per-shop aliases) and the
  AliExpress emails all land in the one Gmail account
  `martin@nakomis.com` (seen in a search on 22 Oct). Gmail already files them under the
  **Purchases** category and Martin's **AliExpress / Amazon / eBay** labels; filter on those
  (`category:purchases`, `label:aliexpress` …) rather than on sender addresses.
- **AliExpress thumbnails: use them (my call).** Fetch the product thumbnail URLs from the
  HTML email and give Haiku the photo plus several thumbnails in one call ("which of these,
  or none?"). Cost is a few cents at most per run. If thumbnails can't be extracted, fall
  back to order-time weighting alone for AliExpress.

**Finding the session.** `~/scripts/cjump` already does description → session:
conversation-memory hybrid search (imported directly from
`~/repos/nakomis/claude-chats/mcp`, env from `meta-mcp/config.toml`), then Haiku via
`claude -p --json-schema` picks one. The finder reuses that approach with a purchase-focused
instruction ("pick the session where Martin decided to buy this"), and adds:
- the matched order's product title as the search query when there is one;
- **time weighting:** prefer sessions active in the hours before the order timestamp;
- **purchase signals:** "added to basket", shop links, "ordered", prices, "which one should I get".
Spike evidence: the microSD breakout found ddr-banging directly; the USB cable tester found
katooni-diy-cable-tester; a vague "Waveshare RP2040/RP2350 board" scattered across four
sessions. Specific names work; vague ones don't, hence orders first.

**Delivering to the session.**
- Every live Claude Code session writes `~/.claude/sessions/<pid>.json` with `sessionId`,
  `messagingSocketPath` (e.g. `/tmp/cc-socks/<pid>.sock`), `status` and `cwd`, plus a key file
  `~/.claude/sessions/<pid>.<hash>.key`. midichlorian's `src/midichlorian/notify.py` shows the
  protocol: connect to the socket, send one JSON line `{"type":"auth","token":…}` then one
  `{"type":"user","message":{"role":"user","content":…}}`. **Verified 22 Oct:** the contents
  of `<pid>.<hash>.key` authenticate that pid's socket; a test message sent this way arrived
  in the target session (it shows as a message from "another Claude session").
- **Terminal.app only (my call):** Martin's sessions run in Terminal.app, which is what cjump
  handles. If an open session has no Terminal tab, still deliver over the socket and say so
  in the notification.
- **Open session:** raise its Terminal tab (cjump's AppleScript), then deliver over the socket.
- **Closed session:** open a Terminal tab in the session's `cwd` and run
  `claude --resume <id> --allow-dangerously-skip-permissions "<message>"` (cjump's flags).
- **The photo** is copied to `/Volumes/share/parcel-finder/YYYY-MM-DD/HHMMSS-<slug>.jpg`
  (the share is mounted on phi) and the message includes that path. The local
  `~/Pictures/ScreenEye/…` original stays as well. *(Folder name is my call.)*

**Ambiguity (decided by Martin).** Go ahead without asking **only** when the photo matched
an order *and* one session clearly stands out for that order. In every other case, show a
pick-list of the top two or three sessions (name, date, one-line reason) and let Martin pick
or cancel. Expect a prompt on perhaps a third of parcels at first; Haiku's self-reported
confidence is not trusted on its own. *(Mechanism is my call: an AppleScript
`choose from list` dialog is the simplest thing that works. "Clearly stands out" thresholds
are the builder's, tuned on the replayable eval.)*

**Sessions on other machines (decided by Martin).** conversation-memory holds transcripts from
other hosts (e.g. `mu`). If the chosen session's transcript isn't on phi (no local
`~/.claude/projects/…/<id>.jsonl`), the finder stops and says so ("that session is on mu"),
naming the session; it doesn't try another session and doesn't ssh anywhere.

**Success notification.** On delivery, one notification: "Identified: <item> → <session name>".

**Logging.** One structured log entry per run (JSON lines, e.g.
`~/Library/Logs/parcel-finder/runs.jsonl`, plus a human-readable `finder.log`) recording:
photo paths; OCR text and timing; rule matched; Haiku requests and responses (no secrets);
the candidate orders and the match; search queries; the candidate sessions with scores and
reasons; the chosen session; delivery method and result; errors. Keep the photos referenced
so a run can be replayed offline (a `replay <run-id>` command is my call and cheap).

**Secrets.** The Anthropic API key currently lives in the keychain under service `cabal-mcp`,
account `anthropic-api-key`; the finder gets its own keychain entry (my call) rather than
borrowing cabal's. Bedrock was tried: the sandbox account lists Haiku 5.5 but returns
"not available for this account" until its Marketplace agreement is accepted. Use the
Claude API.

## Decided by Martin

- Trigger: a second button in ScreenEye ("Open in Claude"), poking a service on the Mac.
- He'll be sat at the Mac when using it.
- The finder only links photo → session; the session takes over.
- The message text above, including the "if you don't recognise it" safety valve.
- No UI for now; good logging instead.
- Use order emails (AliExpress, Amazon, eBay); use the AliExpress API if it's there.
- Photo goes somewhere shared such as `/Volumes/share`.
- Ask (pick-list) unless there's an order match and one clear session.
- A session on another machine: say so and stop.
- Lives in a new repo (created with `/create-repo`); the ScreenEye button is a separate
  change in the mcps repo. Work is tracked in Plane project **HOME**, under its own module
  (epic) for the finder.

## My calls (Martin to check)

- Repo name `parcel-finder` (`nakomis/parcel-finder`); this document moves into it as
  `docs/superpowers/specs/2026-10-22-parcel-finder-design.md`.
- launchd service on loopback; ScreenEye Mac app button first, phone later.
- Gmail via `claude -p` first, own OAuth as a fallback.
- Order window ~60 days, weighting undelivered and just-delivered orders.
- Ambiguity handled with a pick-list dialog rather than guessing.
- `/Volumes/share/parcel-finder/` for photos; logs under `~/Library/Logs/parcel-finder/`.
- Own keychain entry for the Anthropic key.
- Escalate low-confidence identification to Sonnet 5.5 only if Haiku-plus-orders proves
  insufficient (measure first).

## Packaging (my call)

A per-user **LaunchAgent** in Martin's login session (not a daemon), so it shares his
keychain, Claude Code login, `/Volumes/share` mount and Terminal. The first run will trigger
macOS Automation (Terminal) and possibly Local Network prompts; Martin approves them once.
The first build task is a feasibility check from inside the LaunchAgent: `claude -p` with
Gmail, AppleScript to Terminal, keychain read, write to `/Volumes/share`. If the LaunchAgent
context can't get these, the fallback is to host the finder inside the ScreenEye Mac app
process (already signed and permissioned).

## Left to the builder

- Language and structure (the spike is Python; cjump is bash plus inline Python; ScreenEye
  is Swift). Packaging, launchd plist, how the button finds the service.
- Exact rule set, OCR-informativeness threshold, scoring weights, prompt wording.
- How to extract AliExpress thumbnails (and any alt text) from the HTML emails.
- Behaviour when the target session is busy: queue until idle (`status` in the session
  file) versus deliver immediately.
- Tests: a small set of labelled photos (the three from 22 Oct: Adafruit 4682 clone → ddr-banging;
  USB cable tester → probably katooni-diy-cable-tester; Waveshare RP2xxx-Tiny pair → unknown)
  as a replayable eval.

## Cleanup note

The spike pulled eight Ollama models and some detector weights into phi; they are listed in
Claude's memory note `vlm-test-models-on-phi`. Keep `glm-ocr`; the others can go.
