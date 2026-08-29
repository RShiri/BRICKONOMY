"""In-process background refresh worker — one item at a time, polled by the UI.

Two producers feed the same queue:

  start(scope=…)   the Refresh page's "Start scan" button (one at a time)
  enqueue(item_id) a page view of a set whose prices are stale

A single worker thread drains it, so browsing several stale sets queues them
all instead of dropping every request after the first.
"""
import threading
import time
from collections import deque

# Clicking through many stale sets must not build an unbounded backlog.
MAX_QUEUE = 25

_lock = threading.Lock()
_queue = deque()        # ("scope", kwargs) | ("item", item_id, kwargs)
_queued_items = set()   # item ids in _queue, for dedupe
_state = {
    "running": False,
    "scope": None,
    "current_item": None,
    "done": 0,
    "total": 0,
    "errors": [],
    # A set rescan logs the set plus every fig in it, several lines each; 40
    # truncated the start of a run before it could be read.
    "log": deque(maxlen=400),
    "finished_at": None,
}


def status():
    with _lock:
        s = dict(_state)
        s["log"] = list(s["log"])
        s["errors"] = list(s["errors"])
        s["queue"] = [t[1] for t in _queue if t[0] == "item"]
        s["queue_len"] = len(_queue)
        return s


def _log_line(msg):
    with _lock:
        _state["log"].append(msg)


def _progress(done, total, current_item, errors):
    with _lock:
        _state.update(done=done, total=total, current_item=current_item,
                      errors=[f"{i} · {s}: {e}" for i, s, e in errors])


def _spawn_worker():
    """Start the drain thread. Caller must hold _lock and have checked that
    nothing is running."""
    _state.update(running=True, finished_at=None)
    threading.Thread(target=_drain, name="brickonomy-refresh", daemon=True).start()


def start(scope="portfolio", item_id=None, force=False, theme=None,
          inventory_only=False, with_figs=False):
    """Queue a manual scan. Returns False if one is already running or queued
    (the Refresh page disables its button on that).

    with_figs follows a set scan with one scan per minifig in it: scanning a
    set prices the set, never the figures it contains."""
    with _lock:
        if _state["running"] or any(t[0] == "scope" for t in _queue):
            return False
        if item_id:
            _queue.append(("item", item_id,
                           {"force": force, "inventory_only": inventory_only,
                            "with_figs": with_figs}))
            _queued_items.add(item_id)
        else:
            _queue.append(("scope", {"scope": scope, "force": force,
                                     "theme": theme}))
        _state.update(scope=item_id or theme or scope, current_item=None,
                      done=0, total=0, errors=[])
        _state["log"].clear()
        _spawn_worker()
    return True


def start_action(name):
    """Queue a one-off maintenance action (catalog import, static export,
    eBay sign-in check) so the whole toolset is drivable from the web UI and
    not only from the command line. Returns False if something is running."""
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name!r}")
    with _lock:
        if _state["running"] or any(t[0] != "item" for t in _queue):
            return False
        _queue.append(("action", name))
        _state.update(scope=ACTIONS[name][0], current_item=None, done=0,
                      total=0, errors=[])
        _state["log"].clear()
        _spawn_worker()
    return True


def _run_catalog_import(log):
    from .. import db as dbq
    from ..rebrickable import import_catalog

    conn = dbq.connect()
    try:
        counts = import_catalog(conn, with_minifigs=True, log=log)
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        log(f"✔ catalog now holds {total:,} items "
            f"({counts['sets']:,} sets, {counts['minifigs']:,} minifigs)")
    finally:
        conn.close()


def _run_export(log):
    from ..export import export

    pages, jsons = export("docs", get_display_currency(), quiet=True)
    log(f"✔ exported {pages} pages + {jsons} JSON files into docs/")
    log("  Publish with: git add docs && git commit -m \"Update site\" && git push")


def _run_ebay_check(log):
    from ..ebay_login import check

    if not check(log=log):
        log("  Sign in from a terminal: python -m brickonomy.ebay_login")


def get_display_currency():
    from ..config import get_config
    return get_config().display_currency


def _run_bricklink_tree(log):
    """BrickLink's own category tree — themes and subthemes as BrickLink
    files them, which is finer-grained than Rebrickable's themes."""
    from .. import db as dbq
    from ..catalog import sync_tree
    from ..scrapers.bricklink import BrickLinkSource

    conn = dbq.connect()
    try:
        n = sync_tree(conn, BrickLinkSource(), log=log)
        log(f"✔ {n} BrickLink categories imported")
    finally:
        conn.close()


def _run_doctor(log):
    """Answer 'why is this source returning nothing?' from the web UI."""
    from ..doctor import check_brickowl, check_ebay

    for name, fn in (("brickowl", check_brickowl), ("ebay", check_ebay)):
        try:
            fn("75192")
        except Exception as exc:
            log(f"✘ {name} check crashed: {type(exc).__name__}: {exc}")
    log("Full detail is printed in the terminal running the app "
        "(python -m brickonomy.doctor <set> for another item).")


ACTIONS = {
    "catalog": ("full LEGO catalog import", _run_catalog_import),
    "bl_tree": ("BrickLink category tree", _run_bricklink_tree),
    "export": ("static site export", _run_export),
    "ebay_check": ("eBay session check", _run_ebay_check),
    "doctor": ("source diagnosis", _run_doctor),
}


def enqueue(item_id):
    """Queue one item scanned because its page was viewed. Returns the 1-based
    queue position, or None when it was not queued (already pending, already
    scanning, or the queue is full)."""
    with _lock:
        if item_id in _queued_items or _state["current_item"] == item_id:
            return None
        if len(_queue) >= MAX_QUEUE:
            return None
        _queue.append(("item", item_id, {}))
        _queued_items.add(item_id)
        position = len(_queue)
        if not _state["running"]:
            _state.update(scope=item_id, current_item=None, done=0, total=0,
                          errors=[])
            _spawn_worker()
    return position


def _next_task():
    """Pop the next task, or clear `running` and return None. Re-checking the
    queue under the lock is what keeps a task enqueued mid-drain from being
    lost between the last pop and the worker exiting."""
    with _lock:
        if not _queue:
            _state["running"] = False
            _state["current_item"] = None
            _state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return None
        task = _queue.popleft()
        if task[0] == "item":
            _queued_items.discard(task[1])
            _state["scope"] = task[1]
        elif task[0] == "action":
            _state["scope"] = ACTIONS[task[1]][0]
        else:
            _state["scope"] = task[1].get("theme") or task[1]["scope"]
        return task


def _queue_figs_of(set_id, force=False):
    """Queue a price scan for every minifig in a set just scanned.

    Called from the worker after the set itself is done, so the roster is
    already populated even the first time a set is scanned. Each fig is a
    separate scrape per source with a polite delay, so a fig-heavy set can
    take minutes — the queue cap keeps that bounded and says what it dropped.
    """
    from .. import db as dbq

    conn = dbq.connect()
    try:
        fig_ids = [r["fig_id"] for r in dbq.get_set_minifigs(conn, set_id)]
    finally:
        conn.close()
    if not fig_ids:
        return

    queued, skipped, dropped = 0, 0, 0
    with _lock:
        for fig_id in fig_ids:
            if fig_id in _queued_items or _state["current_item"] == fig_id:
                skipped += 1
                continue
            if len(_queue) >= MAX_QUEUE:
                dropped += 1
                continue
            _queue.append(("item", fig_id, {"force": force}))
            _queued_items.add(fig_id)
            queued += 1

    _log_line(f"  ⚙ {queued} minifig(s) queued for pricing")
    if skipped:
        _log_line(f"    ({skipped} already queued or scanning)")
    if dropped:
        _log_line(f"    ✘ {dropped} skipped — queue full at {MAX_QUEUE}; "
                  f"rescan the set again to pick them up")


def _drain():
    from ..refresh import run_refresh

    while True:
        task = _next_task()
        if task is None:
            return
        try:
            if task[0] == "item":
                opts = dict(task[2])
                with_figs = opts.pop("with_figs", False)
                run_refresh(item_id=task[1], progress=_progress, log=_log_line,
                            **opts)
                if with_figs:
                    _queue_figs_of(task[1], force=opts.get("force", False))
            elif task[0] == "action":
                _log_line(f"▶ {ACTIONS[task[1]][0]}…")
                ACTIONS[task[1]][1](_log_line)
            else:
                run_refresh(progress=_progress, log=_log_line, **task[1])
        except Exception as exc:
            _log_line(f"✘ refresh crashed: {type(exc).__name__}: {exc}")


def reset():
    """Test helper: drop any queued work and clear the state."""
    with _lock:
        _queue.clear()
        _queued_items.clear()
        _state.update(running=False, scope=None, current_item=None, done=0,
                      total=0, errors=[], finished_at=None)
        _state["log"].clear()
