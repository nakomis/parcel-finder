"""One run: photo → item → order → session → delivered message."""
from dataclasses import dataclass
from datetime import datetime
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


def collect_orders(sync_if_stale: bool = True) -> tuple[list, str | None]:
    """Recent orders from the cache (synced from Gmail when stale) plus AliExpress details.
    AliExpress problems come back as a message, never an exception."""
    from .orders import cache
    from .orders.aliexpress import AliExpress, ApiError, Reauthorise
    c = cache.OrderCache()
    if sync_if_stale and c.stale():
        cache.sync(c)
    found, problem = list(c.orders), None
    try:
        for o in AliExpress.from_keychain().orders(list(c.ae_ids)):
            o.thumbnail_urls = c.ae_ids.get(o.order_id, [])
            found.append(o)
    except (Reauthorise, ApiError, config.MissingSecret) as e:
        problem = str(e)
    return found, problem


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
        orders, problem = deps.collect_orders()
        log.event("orders", count=len(orders), problem=problem)
        if problem:
            deps.notify("parcel-finder", f"AliExpress unavailable ({problem}); if it has lapsed, "
                                         "run `parcel-finder authorise`")
        match = deps.match_order(photo, ident, orders) if orders else None
        log.event("match", order=match.order.title if match else None, shop=match.order.shop if match else None,
                  confidence=match.confidence if match else None, reason=match.reason if match else None)
        item = match.order.title if match else ident.item
        candidates = deps.search(item, match.order.ordered_at if match else None)
        log.event("search", query=item, candidates=[(c.title, c.session_id, round(c.score, 1)) for c in candidates[:8]])
        choice = deps.choose(ident, match, candidates)
        log.event("choice", chosen=choice.chosen.session_id if choice.chosen else None, clear=choice.clear,
                  reason=choice.reason)
        if not choice.candidates:
            return done("not-found", detail="no candidate sessions",
                        note=f"Couldn't find the conversation for the {item}; see run {log.run_id}")
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
        note = f"Identified: {item} → {title}" + ("" if shared else " (photo not copied to the share)")
        return done(status, session_id, note=note)
    except Exception as e:  # every failure ends as a logged, notified outcome
        return done("error", detail=repr(e), note=f"parcel-finder failed: {e} (run {log.run_id})")


def _cwd_for(choice, session_id: str) -> Path:
    project = next((c.project for c in choice.candidates if c.session_id == session_id), "")
    return Path(project) if project and Path(project).is_dir() else config.HOME
