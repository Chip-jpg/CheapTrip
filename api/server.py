"""
The desktop app's local API (B38).

Serves JSON for every screen, live events, and the app's UI files, on
127.0.0.1 only. Protection against other programs and web pages:

- a random token per launch, required on every /api request (a cookie set
  when the app opens `/?token=...`, or an `Authorization: Bearer` header);
- requests whose Host isn't this server's own address are refused, so a web
  page can't reach it through DNS rebinding;
- no CORS headers, so other origins can't read responses.

Routes (all under /api/v1):

  GET  /status                         top bar: state, next/last search, learning progress, attention
  GET  /deals                          Deals: unusually cheap + best of the rest, with filters
  GET  /deals/{id}                     Deal detail, with both charts' data
  POST /deals/{id}/hide
  GET  /destinations?tab=priority|muted|seen
  POST|DELETE /destinations/{code}/priority|mute
  GET  /airports?q=                    autocomplete
  GET  /activity                       sources, alerts log, search history
  GET  /settings   PUT /settings
  GET  /setup/checks                   the doctor checks
  POST /setup/test-telegram
  POST /setup/test-notification       {"channel": "desktop"|"telegram"} optional: all that are on;
                                       {"kind": "deal"|"error_fare"|"digest"|"notice"}: that preview, on the desktop
  GET  /notifications/previews         the notifications CheapTrip would show now, as Windows will show them
  GET  /notifications/image/{name}     a preview's picture
  POST /engine/search-now   /engine/pause   /engine/resume
  GET  /events                         server-sent events from the engine
  POST /app/show   {"route": ...}      show the app's window (a second launch, a cheaptrip:// link)
  POST /app/hide                       hide the window to the tray (the desktop app)
  POST /app/open   {"what": "logs"|"data"|"notification-settings"}  open a folder or Windows' settings
  POST /app/quit                       quit the app
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import signal
import sys
import webbrowser
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

import control
from api.records import deal_record, destination_rows, matches, place
from api.settings_io import SOURCE_SWITCHES, apply_settings, read_settings
from config import get_settings
from desktop.links import clean_route
from desktop.windows import open_path
from preferences import get_preferences
from scrapers.health_monitor import get_health_monitor
from storage.database import (
    alert_log,
    count_alerts_sent_last_hour,
    count_pending_instants,
    get_deal,
    get_recent_deals,
    hide_deal,
    init_db,
    latest_alerts_for,
    recent_cycles,
    seen_fares,
)
from storage.models import AlertTier
from storage.price_analytics import fare_comparison
from utils import airports
from utils.events import get_event_bus
from utils.logging_config import get_logger
from utils.paths import bundle_dir
from utils.timeutil import utcnow
from utils.version import __version__

log = get_logger(__name__)

API = "/api/v1"
TOKEN_COOKIE = "cheaptrip_token"

ENGINE = web.AppKey("engine", object)
TOKEN = web.AppKey("token", str)
STATIC_DIR = web.AppKey("static_dir", object)
CLOSING = web.AppKey("closing", asyncio.Event)
SHELL = web.AppKey("shell", object)  # the app around the API: show(route), quit()
SSE_PING_S = 15
PROBLEM_STATUSES = ("FAILING", "STALE", "DEGRADED")

# Which Settings switch turns each source on or off (None: always on, or on when its key is set)
SWITCH_OF = {
    "ryanair": "enable_ryanair", "piratinviaggio": "enable_piratinviaggio",
    "piratinviaggio_hotels": "enable_piratinviaggio", "skyscanner_api": "enable_skyscanner",
    "google_hotels": "enable_google_hotels", "booking_com_api": "enable_booking_html",
    "secret_flying": "enable_secret_flying", "going": "enable_going",
    "holiday_pirates": "enable_holiday_pirates", "holiday_pirates_hotels": "enable_holiday_pirates",
}
assert set(SWITCH_OF.values()) <= set(SOURCE_SWITCHES)

_FALLBACK_PAGE = """<!doctype html><meta charset="utf-8"><title>CheapTrip</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 3rem auto">
<h1>CheapTrip is running</h1>
<p>The app's screens haven't been built in this copy (run <code>npm ci &amp;&amp; npm run build</code> in
<code>ui/</code>). The API is at <a href="/api/v1/status">/api/v1/status</a>.</p></body>"""


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str))


def _error(message: str, status: int) -> web.Response:
    return _json({"error": message}, status=status)


def ui_dir() -> Optional[Path]:
    """The built UI (ui/dist, bundled into the Windows app), when there is one."""
    path = bundle_dir() / "ui" / "dist"
    return path if (path / "index.html").exists() else None


# ── Guard ─────────────────────────────────────────────────────────────────────

def _own_hosts(request: web.Request) -> set:
    sockname = request.transport.get_extra_info("sockname") if request.transport else None
    port = sockname[1] if sockname else None
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


@web.middleware
async def guard(request: web.Request, handler) -> web.StreamResponse:
    if request.headers.get("Host", "") not in _own_hosts(request):
        return _error("forbidden host", 403)
    if request.path.startswith("/api/"):
        supplied = request.cookies.get(TOKEN_COOKIE) or request.headers.get("Authorization", "")
        supplied = supplied.removeprefix("Bearer ").strip()
        if not supplied or not secrets.compare_digest(supplied, request.app[TOKEN]):
            return _error("open CheapTrip from the app (missing or wrong token)", 401)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ValueError as exc:  # bad input (pydantic's ValidationError is a ValueError too)
        return _error(str(exc), 400)
    except Exception as exc:
        log.error("api_error", path=request.path, error=str(exc), exc_info=True)
        return _error("something went wrong; see the log", 500)


# ── UI files ──────────────────────────────────────────────────────────────────

async def index(request: web.Request) -> web.StreamResponse:
    token = request.query.get("token")
    if token is not None:
        if not secrets.compare_digest(token, request.app[TOKEN]):
            return _error("wrong token", 401)
        response = web.HTTPFound("/")  # drop the token from the address bar
        response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="Strict", path="/")
        raise response
    static = request.app[STATIC_DIR]
    if static is None:
        return web.Response(text=_FALLBACK_PAGE, content_type="text/html")
    return web.FileResponse(static / "index.html")


async def static_file(request: web.Request) -> web.StreamResponse:
    static = request.app[STATIC_DIR]
    if static is None:
        raise web.HTTPNotFound()
    target = (static / request.match_info["path"]).resolve()
    if not target.is_relative_to(static.resolve()) or not target.is_file():
        return web.FileResponse(static / "index.html")  # client-side routes
    return web.FileResponse(target)


# ── Status and deals ──────────────────────────────────────────────────────────

async def status(request: web.Request) -> web.Response:
    kind = getattr(request.app[SHELL], "kind", None)  # "desktop" | "browser": which controls the screens offer
    return _json({**await full_status(request.app[ENGINE]), "app": kind})


async def full_status(engine: Any) -> Dict[str, Any]:
    """The top bar's (and the tray's) status: the engine's snapshot plus what needs attention."""
    snapshot = await engine.status()
    settings = get_settings()
    last = snapshot["last_cycle"]
    attention: List[str] = []
    if last and last["status"] == "failed":
        attention.append(f"The last search failed: {last['error']}")
    for health in await get_health_monitor().get_health_report():
        if health.status in PROBLEM_STATUSES:
            attention.append(f"{health.source_id}: {health.status.lower()}")
    if snapshot["state"] == "running" and attention:
        snapshot["state"] = "attention"
    return {
        **snapshot,
        "attention": attention,
        "learning": {"trips": last["trips"], "with_usual_price": last["with_usual_price"]} if last else None,
        "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "timezone": settings.timezone,
        "version": __version__,
    }


def _filters(request: web.Request) -> Dict[str, Any]:
    query = request.query

    def listed(name: str) -> List[str]:
        return [v for v in query.get(name, "").split(",") if v]

    return {
        "types": listed("types"), "lengths": listed("lengths"),
        "max_price": float(query["max_price"]) if query.get("max_price") else None,
        "date_from": query.get("from"), "date_to": query.get("to"), "q": query.get("q"),
        "priority_only": query.get("priority_only") in ("1", "true"),
    }


_SORTS = {
    "price": lambda r: (r["price"], r["depart_date"] or ""),
    "discount": lambda r: (-(r["discount_pct"] or 0), r["price"]),
    "date": lambda r: (r["depart_date"] or "9999", r["price"]),
}


async def _comparison(row: Any) -> Optional[Dict[str, Any]]:
    """The "how this fare compares" data for a dated flight (None for a deal-site post or a hotel)."""
    leg = row.trip.outbound_flight
    return await fare_comparison(leg) if leg and leg.departure_date and not leg.is_feed_deal else None


async def deals(request: web.Request) -> web.Response:
    rows = await get_recent_deals(hours=24)
    prefs = get_preferences()
    notified = await latest_alerts_for([row.trip.hash for row in rows])
    records = [deal_record(row, prefs, notified.get(row.trip.hash)) for row in rows]
    by_id = {row.trip.hash: row for row in rows}
    filters = _filters(request)
    sort = request.query.get("sort")

    instant_all = sorted((r for r in records if r["tier"] == "instant"), key=_SORTS["discount"])
    instant = sorted((r for r in instant_all if matches(r, filters)), key=_SORTS.get(sort, _SORTS["discount"]))
    for record, comparison in zip(instant, await asyncio.gather(*(_comparison(by_id[r["id"]]) for r in instant))):
        record["comparison"] = comparison  # each card's "30-day fare range"
    best_per_route: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if record["tier"] == "digest":
            current = best_per_route.get(record["route"])
            if current is None or record["price"] < current["price"]:
                best_per_route[record["route"]] = record
    digest = sorted((r for r in best_per_route.values() if matches(r, filters)), key=_SORTS.get(sort, _SORTS["price"]))

    finished = [c for c in await recent_cycles(limit=5) if c["status"] != "running"]
    # "Best today": the most unusually cheap deal, else the cheapest of all
    best = instant_all[0] if instant_all else min(records, key=lambda r: r["price"]) if records else None
    return _json({
        "summary": {
            "unusually_cheap": len(instant_all),
            "best": best,
            "last_search": {"at": finished[0]["finished_at"], "fares": finished[0]["fares"]} if finished else None,
        },
        "instant": instant,
        "digest": digest,
    })


async def deal_detail(request: web.Request) -> web.Response:
    row = await get_deal(request.match_info["id"])
    if row is None:
        return _error("no such deal", 404)
    notified = await latest_alerts_for([row.trip.hash])
    record = deal_record(row, get_preferences(), notified.get(row.trip.hash))
    record["comparison"] = await _comparison(row)
    record["verdict"] = row.trip.verdict
    record["feasibility_notes"] = list(row.trip.feasibility_notes)
    return _json(record)


async def deal_hide(request: web.Request) -> web.Response:
    deal_id = request.match_info["id"]
    if not await hide_deal(deal_id):
        return _error("no such deal", 404)
    get_event_bus().publish("deal_hidden", id=deal_id)
    return _json({"id": deal_id, "hidden": True})


# ── Destinations ──────────────────────────────────────────────────────────────

_BLANK_STATS = {"best_price": None, "best_depart": None, "best_return": None, "best_route": None,
                "usual_price": None, "fares": 0, "last_seen": None, "trend": []}


async def destinations(request: web.Request) -> web.Response:
    tab = request.query.get("tab", "seen")
    prefs = get_preferences()
    stats = destination_rows(await seen_fares(utcnow() - timedelta(days=30)))

    def row(code: str) -> Dict[str, Any]:
        return {**_BLANK_STATS, **place(code), **stats.get(code, {}), "code": code,
                "is_priority": code in prefs.priority_destinations, "is_muted": code in prefs.excluded_destinations}

    if tab == "priority":
        codes = prefs.priority_destinations
    elif tab == "muted":
        codes = prefs.excluded_destinations
    elif tab == "seen":
        codes = sorted(stats, key=lambda c: stats[c]["best_price"])
    else:
        return _error("tab must be priority, muted or seen", 400)
    return _json({"tab": tab, "destinations": [row(code) for code in codes]})


async def destination_toggle(request: web.Request) -> web.Response:
    code = control.airport_code(request.match_info["code"])
    if code is None:
        return _error("unknown airport code", 400)
    on = request.method == "POST"
    if request.match_info["what"] == "priority":
        await control.set_priority(code, on)
    else:
        await control.set_muted(code, on)
    prefs = get_preferences()
    return _json({**place(code), "is_priority": code in prefs.priority_destinations,
                  "is_muted": code in prefs.excluded_destinations})


async def airport_search(request: web.Request) -> web.Response:
    found = airports.search(request.query.get("q", ""), limit=int(request.query.get("limit", 8)))
    return _json([{"code": a.iata, "name": a.name, "city": a.city, "country": a.country} for a in found])


# ── Activity ──────────────────────────────────────────────────────────────────

async def activity(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    health = {h.source_id: h for h in await get_health_monitor().get_health_report()}
    sources = []
    for scraper in engine.sources:
        h = health.get(scraper.source_id)
        sources.append({
            "id": scraper.source_id,
            "enabled": bool(scraper.enabled),
            "reason": getattr(scraper, "disabled_reason", None),
            "status": h.status if h else ("DISABLED" if not scraper.enabled else "UNKNOWN"),
            "last_count": h.last_count if h else None,
            "last_run_at": h.last_run_at if h else None,
            "last_success_at": h.last_success_at if h else None,
            "last_error": h.last_error if h else None,
            "consecutive_failures": h.consecutive_failures if h else 0,
            "switch": SWITCH_OF.get(scraper.source_id),
        })
    return _json({
        "sources": sources,
        "alerts": {
            "log": await alert_log(limit=50),
            "per_hour_limit": get_settings().instant_alerts_per_hour,
            "sent_last_hour": await count_alerts_sent_last_hour(AlertTier.INSTANT),
            "waiting": await count_pending_instants(),
        },
        "searches": await recent_cycles(limit=30),
    })


# ── Settings and setup ────────────────────────────────────────────────────────

async def settings_get(request: web.Request) -> web.Response:
    return _json(read_settings())


async def settings_put(request: web.Request) -> web.Response:
    result = await apply_settings(await request.json(), request.app[ENGINE])
    return _json({**result, "settings": read_settings()})


async def setup_checks(request: web.Request) -> web.Response:
    from utils.doctor import run_checks

    return _json({"checks": [dataclasses.asdict(check) for check in await run_checks()]})


async def setup_test_telegram(request: web.Request) -> web.Response:
    from utils.doctor import send_test_message

    sent, detail = await send_test_message()
    return _json({"sent": sent, "detail": detail}, status=200 if sent else 502)


async def setup_test_notification(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    channel, kind = body.get("channel"), body.get("kind")
    if channel not in (None, "desktop", "telegram"):
        raise ValueError('channel must be "desktop" or "telegram"')
    notifier = request.app[ENGINE].notifier
    if kind not in (None, "test"):  # one of the Notifications screen's previews, on the desktop
        desktop = getattr(notifier, "desktop", None)
        if desktop is None or not get_settings().notify_desktop:
            return _error("desktop notifications are off", 409)
        toasts = await _preview_toasts(desktop)
        if kind not in toasts:
            raise ValueError(f"no {kind} notification to show right now")
        toast, _ = toasts[kind]
        return _json({"sent": {"desktop": await desktop.show(toast)}})
    results = await notifier.send_test(only=channel) if hasattr(notifier, "send_test") else {}
    if not results:
        return _error("no notification channel is on" + (f" for {channel}" if channel else ""), 409)
    return _json({"sent": results}, status=200 if any(results.values()) else 502)


# ── Notifications (the Notifications screen) ──────────────────────────────────

async def _preview_toasts(desktop: Any = None) -> Dict[str, Any]:
    """{kind: (toast, is_example)}: what CheapTrip would show now, from today's deals and sources."""
    from notifier.desktop import deal_toast, digest_toast, notice_toast, test_toast
    from scheduler.runner import select_digest_trips
    from utils.sources import source_name

    rows = await get_recent_deals(hours=24)
    trips = [row.trip for row in rows]
    instant = sorted((row.trip for row in rows if row.alert_tier == "instant"),
                     key=lambda t: (-(t.discount_pct or 0), t.total_cost_eur))
    toasts: Dict[str, Any] = {}
    best = next((t for t in instant if not t.is_error_fare), None) or min(trips, key=lambda t: t.total_cost_eur, default=None)
    if best is not None:
        toast = deal_toast(best)
        if desktop is not None:
            toast.image = await desktop.deal_image(best)
        toasts["deal"] = (toast, False)
    error = next((t for t in instant if t.is_error_fare), None)
    if error is not None:
        toasts["error_fare"] = (deal_toast(error), False)
    digest = select_digest_trips([row.trip for row in rows if row.alert_tier == "digest"])
    if digest:
        toasts["digest"] = (digest_toast(digest), False)
    problem = next((h for h in await get_health_monitor().get_health_report() if h.status in PROBLEM_STATUSES), None)
    if problem is not None:
        toasts["notice"] = (notice_toast(f"{source_name(problem.source_id)} isn't working",
                                         f"{problem.consecutive_failures} searches failed in a row. "
                                         "Deals from it may be missing."), False)
    else:
        toasts["notice"] = (notice_toast("Google Flights isn't working",
                                         "3 searches failed in a row. Deals from it may be missing."), True)
    toasts["test"] = (test_toast(), False)
    return toasts


async def notification_previews(request: web.Request) -> web.Response:
    desktop = getattr(request.app[ENGINE].notifier, "desktop", None)
    previews = []
    for kind, (toast, example) in (await _preview_toasts(desktop)).items():
        image = f"{API}/notifications/image/{toast.image.name}" if toast.image else None
        previews.append({"kind": kind, "example": example, "image": image, **toast.preview()})
    settings = get_settings()
    return _json({
        "previews": previews,
        "desktop": {"available": desktop is not None, "on": settings.notify_desktop, "sound": settings.notify_sound},
        "app_id": "CheapTrip",
    })


async def notification_image(request: web.Request) -> web.Response:
    folder = (Path.cwd() / "data" / "toasts").resolve()
    target = (folder / request.match_info["name"]).resolve()
    if target.suffix != ".png" or not target.is_relative_to(folder) or not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


# ── Engine controls ───────────────────────────────────────────────────────────

async def search_now(request: web.Request) -> web.Response:
    return _json({"started": await request.app[ENGINE].search_now()})


async def pause(request: web.Request) -> web.Response:
    body = await request.json()
    until = await request.app[ENGINE].pause(**control.pause_end(body.get("hours"), body.get("until")))
    return _json({"paused_until": until.isoformat()})


async def resume(request: web.Request) -> web.Response:
    await request.app[ENGINE].resume()
    return _json({"paused_until": None})


# ── The app ───────────────────────────────────────────────────────────────────

async def app_show(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    route = body.get("route")
    if route is not None:
        route = clean_route(str(route))
        if route is None:
            raise ValueError("unknown screen")
    request.app[SHELL].show(route)
    log.info("app_show", route=route)
    return _json({"shown": True})


async def app_hide(request: web.Request) -> web.Response:
    hide = getattr(request.app[SHELL], "hide", None)
    if hide is None:
        return _error("only the desktop app has a window to hide", 409)
    hide()
    return _json({"hidden": True})


def openable(what: str) -> Optional[str]:
    """The folders and settings page the screens may open: logs, the data folder, Windows' notification settings."""
    if what == "logs":
        return str(Path(get_settings().log_file).resolve().parent)
    if what == "data":
        return str(Path.cwd())  # .env, config/, data/ and logs/ (the app home in the Windows app)
    if what == "notification-settings" and sys.platform == "win32":
        return "ms-settings:notifications"
    return None


async def app_open(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    target = openable(str(body.get("what", "")))
    if target is None:
        raise ValueError('what must be "logs", "data" or (on Windows) "notification-settings"')
    await asyncio.to_thread(open_path, target)
    return _json({"opened": target})


async def app_quit(request: web.Request) -> web.Response:
    request.app[SHELL].quit()
    return _json({"quitting": True})


class BrowserShell:
    """The app around the API in `main.py ui`: screens open in the browser."""

    kind = "browser"

    def __init__(self, stop: asyncio.Event) -> None:
        self.server: Optional["ApiServer"] = None
        self._stop = stop

    def show(self, route: Optional[str] = None) -> None:
        if self.server is not None:
            webbrowser.open(self.server.link(route or ""))

    def quit(self) -> None:
        self._stop.set()

    def notification_action(self, kind: str, value: str) -> Any:
        """A notification's buttons: Details opens the deal in the browser; Mute mutes."""
        if kind == "mute":
            return control.set_muted(value, True)
        self.show(f"deals/{value}" if kind == "details" else value)
        return None


# ── Live events ───────────────────────────────────────────────────────────────

async def events(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    closing = request.app[CLOSING]
    loop = asyncio.get_running_loop()
    with get_event_bus().subscribe() as queue:
        try:
            await response.write(b": connected\n\n")
            last_write = loop.time()
            while not closing.is_set():  # wakes every second to notice the app closing
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if loop.time() - last_write >= SSE_PING_S:
                        await response.write(b": ping\n\n")
                        last_write = loop.time()
                    continue
                payload = json.dumps(event, default=str)
                await response.write(f"event: {event['type']}\ndata: {payload}\n\n".encode())
                last_write = loop.time()
        except (ConnectionResetError, RuntimeError):  # the screen closed
            pass
    return response


# ── App ───────────────────────────────────────────────────────────────────────

async def _on_shutdown(app: web.Application) -> None:
    app[CLOSING].set()  # ends open event streams


def create_app(engine: Any, token: str, static_dir: Optional[Path] = None, shell: Any = None) -> web.Application:
    app = web.Application(middlewares=[guard])
    app[ENGINE] = engine
    app[TOKEN] = token
    app[STATIC_DIR] = static_dir
    app[CLOSING] = asyncio.Event()
    app[SHELL] = shell
    app.on_shutdown.append(_on_shutdown)
    app.router.add_get(f"{API}/status", status)
    app.router.add_get(f"{API}/deals", deals)
    app.router.add_get(f"{API}/deals/{{id}}", deal_detail)
    app.router.add_post(f"{API}/deals/{{id}}/hide", deal_hide)
    app.router.add_get(f"{API}/destinations", destinations)
    app.router.add_route("POST", f"{API}/destinations/{{code}}/{{what:priority|mute}}", destination_toggle)
    app.router.add_route("DELETE", f"{API}/destinations/{{code}}/{{what:priority|mute}}", destination_toggle)
    app.router.add_get(f"{API}/airports", airport_search)
    app.router.add_get(f"{API}/activity", activity)
    app.router.add_get(f"{API}/settings", settings_get)
    app.router.add_put(f"{API}/settings", settings_put)
    app.router.add_get(f"{API}/setup/checks", setup_checks)
    app.router.add_post(f"{API}/setup/test-telegram", setup_test_telegram)
    app.router.add_post(f"{API}/setup/test-notification", setup_test_notification)
    app.router.add_get(f"{API}/notifications/previews", notification_previews)
    app.router.add_get(f"{API}/notifications/image/{{name}}", notification_image)
    app.router.add_post(f"{API}/engine/search-now", search_now)
    app.router.add_post(f"{API}/engine/pause", pause)
    app.router.add_post(f"{API}/engine/resume", resume)
    app.router.add_get(f"{API}/events", events)
    app.router.add_post(f"{API}/app/open", app_open)
    if shell is not None:
        app.router.add_post(f"{API}/app/show", app_show)
        app.router.add_post(f"{API}/app/hide", app_hide)
        app.router.add_post(f"{API}/app/quit", app_quit)
    app.router.add_get("/", index)
    app.router.add_get("/{path:.+}", static_file)
    return app


class ApiServer:
    """The API and UI on 127.0.0.1, on a free port unless one is given."""

    def __init__(
        self, engine: Any, token: Optional[str] = None, static_dir: Optional[Path] = None, shell: Any = None,
    ) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.app = create_app(engine, self.token, static_dir, shell)
        self.port: Optional[int] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self, port: int = 0) -> int:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", port).start()
        self.port = self._runner.addresses[0][1]
        log.info("api_started", port=self.port)
        return self.port

    @property
    def url(self) -> str:
        """The address that opens the app, token included."""
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def link(self, route: str) -> str:
        """The address of one screen, e.g. "deals/<id>" (the fragment survives the token redirect)."""
        return f"{self.url}#/{route}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


async def run_ui(port: int = 0, open_browser: bool = True) -> None:
    """`main.py ui`: the engine plus the API and UI in your browser, until Ctrl-C."""
    from notifier.desktop import DesktopNotifier
    from scheduler.engine import Engine

    stop = asyncio.Event()
    shell = BrowserShell(stop)
    engine = Engine(desktop=DesktopNotifier(on_action=shell.notification_action,
                                            image_folder=Path.cwd() / "data" / "toasts"))
    server = shell.server = ApiServer(engine, static_dir=ui_dir(), shell=shell)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await init_db()  # before the screens can ask for anything
        await server.start(port)
        await engine.start()
        print(f"CheapTrip is running at {server.url}\nPress Ctrl+C to stop.", flush=True)
        if open_browser:
            webbrowser.open(server.url)
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
        await engine.stop()
