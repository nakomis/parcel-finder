"""One run: photo → item → order → session → delivered message."""
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import config
from .runlog import RunLog


@dataclass
class Outcome:
    status: str  # delivered | resumed | cancelled | elsewhere | not-found | dry-run | error
    run_id: str
    session_id: str | None
    detail: str


def _start_background_sync() -> None:
    """Refresh the order cache without making anyone wait (a Gmail sync takes minutes)."""
    subprocess.Popen([sys.executable, "-m", "parcel_finder.cli", "sync"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def collect_orders() -> tuple[list, str | None]:
    """Recent orders from the local store: no network calls. A stale store, or AliExpress orders
    whose details haven't been fetched yet, start a background sync; meanwhile those orders fall
    back to their emails (untitled; matched by thumbnail)."""
    from .orders import cache
    c = cache.OrderCache()
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.ORDER_WINDOW_DAYS)
    details, ae_ids = c.ae_details(), c.ae_ids
    others = [o for o in c.orders if o.shop != "aliexpress"]
    ae_email = [o for o in c.orders if o.shop == "aliexpress" and _aware(o.ordered_at) >= cutoff]
    # An order whose details have no live lines was cancelled: it stays out.
    fetched = [replace(line, thumbnail_urls=ae_ids.get(oid, []))
               for oid, (_, lines) in details.items() for line in lines if _aware(line.ordered_at) >= cutoff]
    fallback = [replace(o, thumbnail_urls=ae_ids.get(o.order_id, o.thumbnail_urls))
                for o in ae_email if o.order_id not in details]
    if c.stale() or fallback:
        _start_background_sync()
    return others + fetched + fallback, c.ae_problem


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@dataclass
class Deps:
    identify: Callable
    collect_orders: Callable
    match_order: Callable
    search: Callable
    choose: Callable
    ui_choose: Callable
    local_transcript: Callable
    copy_photo: Callable
    live_session: Callable
    send: Callable
    raise_tab: Callable
    resume: Callable
    notify: Callable
    log_dir: Path = config.LOG_DIR

    @classmethod
    def real(cls) -> "Deps":
        from . import delivery, identify, sessions, ui
        from .orders import match
        return cls(identify.identify, collect_orders, match.match_order, sessions.search, sessions.choose,
                   ui.choose, sessions.local_transcript, delivery.copy_photo, delivery.live_session,
                   delivery.send, delivery.raise_tab, delivery.resume_in_new_tab, ui.notify)


def run(photo: Path, deps: Deps | None = None, dry_run: bool = False) -> Outcome:
    from .delivery import message
    deps = deps or Deps.real()
    log = RunLog.start(photo, deps.log_dir)

    def done(status: str, session_id: str | None = None, detail: str = "", note: str | None = None) -> Outcome:
        if note:
            deps.notify("parcel-finder", note)
        log.finish(status, session=session_id, detail=detail)
        return Outcome(status, log.run_id, session_id, detail)

    try:
        ident = deps.identify(photo, log)
        try:
            orders, problem = deps.collect_orders()
        except Exception as e:  # orders are optional: carry on with the identification alone
            orders, problem = [], f"orders unavailable: {e}"
        log.event("orders", count=len(orders), problem=problem)
        if problem:
            deps.notify("parcel-finder", f"AliExpress unavailable ({problem}); if it has lapsed, "
                                         "run `parcel-finder authorise`")
        try:
            match = deps.match_order(photo, ident, orders) if orders else None
        except Exception as e:
            log.event("match-error", error=repr(e))
            match = None
        log.event("match", order=match.order.title if match else None, shop=match.order.shop if match else None,
                  confidence=match.confidence if match else None, reason=match.reason if match else None)
        titled = match is not None and bool(match.order.title)
        item = match.order.title if titled and match.confidence == "high" else ident.item
        from .identify import search_terms
        # A titled order matched with at least medium confidence is the sharpest query; otherwise
        # search on what we can see (a low-confidence or untitled match would only mislead).
        query = match.order.title if titled and match.confidence != "low" else search_terms(ident)
        candidates = deps.search(query, match.order.ordered_at if match else None)
        log.event("search", query=query, candidates=[(c.title, c.session_id, round(c.score, 1)) for c in candidates[:8]])
        try:
            choice = deps.choose(ident, match, candidates)
        except Exception as e:  # without Haiku's pick, ask Martin from the search ranking
            log.event("choice-error", error=repr(e))
            from .sessions import SessionChoice
            choice = SessionChoice(candidates[0] if candidates else None, False, candidates[:8], f"choice failed: {e}")
        log.event("choice", chosen=choice.chosen.session_id if choice.chosen else None, clear=choice.clear,
                  reason=choice.reason)
        if not choice.candidates:
            return done("not-found", detail="no candidate sessions",
                        note=f"Couldn't find the conversation for the {item}; see run {log.run_id}")
        if dry_run and not choice.clear:
            return done("dry-run", choice.chosen.session_id if choice.chosen else None,
                        detail="would ask: " + ", ".join(c.title for c in choice.candidates[:3]))
        session_id = choice.chosen.session_id if choice.clear else deps.ui_choose(choice.candidates, item)
        if not session_id:
            return done("cancelled")
        title = next((c.title for c in choice.candidates if c.session_id == session_id), session_id)
        if not deps.local_transcript(session_id):
            return done("elsewhere", session_id,
                        note=f"The conversation for the {item} ({title}) is not on this Mac.")
        if dry_run:
            return done("dry-run", session_id, detail=f"would deliver to {title}")
        dest, shared = deps.copy_photo(photo, item, datetime.now())
        text = message(item, dest)
        live = deps.live_session(session_id)
        if live:
            raised = bool(live.tty) and deps.raise_tab(live.tty)
            deps.send(live, text)
            status = "delivered"
        else:
            raised = False
            deps.resume(_cwd_for(choice, session_id), session_id, text)
            status = "resumed"
        log.event("deliver", session=session_id, live=bool(live), raised=raised, photo=str(dest), shared=shared)
        note = (f"Identified: {item} → {title}" + ("" if shared else " (photo not copied to the share)")
                + (" (no Terminal tab; delivered in the background)" if live and not raised else ""))
        return done(status, session_id, note=note)
    except Exception as e:  # every failure ends as a logged, notified outcome
        return done("error", detail=repr(e), note=f"parcel-finder failed: {e} (run {log.run_id})")


def _cwd_for(choice, session_id: str) -> Path:
    project = next((c.project for c in choice.candidates if c.session_id == session_id), "")
    return Path(project) if project and Path(project).is_dir() else config.HOME
