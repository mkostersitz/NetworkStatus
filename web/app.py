"""FastAPI web server — runs the monitor loop in a background thread."""
from __future__ import annotations
import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Ensure the repo root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from netmon import db, measure
from netmon.alerts import AlertManager, Thresholds, load as load_alerts, save as save_alerts, alert_queue, DEFAULT_CFG

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = db.DEFAULT_DB

app = FastAPI(title="NetMon", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Shared monitor state ─────────────────────────────────────────────────────

_state: dict = {
    "latest": None,       # most recent latency check result
    "latest_speed": None, # most recent speed result
    "check_count": 0,
    "fail_count": 0,
    "monitor_running": False,
    "speed_running": False,
}
_state_lock = threading.Lock()


# ── SSE broadcast ─────────────────────────────────────────────────────────────
# We keep a list of asyncio Queues, one per connected SSE client.
_sse_listeners: list[asyncio.Queue] = []
_sse_loop: Optional[asyncio.AbstractEventLoop] = None


def _broadcast(event: str, data: dict):
    """Push an event to all connected SSE clients (called from any thread)."""
    if _sse_loop is None:
        return
    payload = json.dumps(data)
    for q in list(_sse_listeners):
        try:
            _sse_loop.call_soon_threadsafe(q.put_nowait, (event, payload))
        except Exception:
            pass


def _flush_alerts():
    """Drain alert_queue to SSE — call after any check_* that may have fired."""
    while alert_queue:
        _broadcast("alert", alert_queue.popleft())


# ── Monitor background thread ─────────────────────────────────────────────────

_monitor_thread: Optional[threading.Thread] = None
_monitor_stop = threading.Event()


def _monitor_loop(host: str, interval: int, ping_count: int,
                  speed_interval: int, alerter: Optional[AlertManager]):
    conn = db.get_conn(DB_PATH)
    last_speed_time = 0.0
    last_dl: Optional[float] = None
    last_ul: Optional[float] = None

    show_speed = bool(
        alerter and (alerter.t.download_mbps is not None or alerter.t.upload_mbps is not None)
    )
    if show_speed and speed_interval == 0:
        speed_interval = 3600

    def do_check():
        nonlocal last_dl, last_ul
        r = measure.measure_latency(host, count=ping_count)
        with _state_lock:
            _state["check_count"] += 1
            if not r["success"]:
                _state["fail_count"] += 1
            _state["latest"] = {**r, "ts": r["ts"].isoformat(),
                                 "dl_mbps": last_dl, "ul_mbps": last_ul}
        db.insert_check(conn, r["ts"], host,
                        r.get("latency_ms"), r.get("jitter_ms"),
                        r.get("pkt_loss", 100.0), r["success"])
        if alerter:
            alerter.t = load_alerts()   # pick up any threshold changes from the UI
            alerter.check_latency(r.get("latency_ms"), r.get("jitter_ms"),
                                  r.get("pkt_loss"), host)
            _flush_alerts()
        _broadcast("check", _state["latest"])

    def do_speed():
        nonlocal last_dl, last_ul
        with _state_lock:
            _state["speed_running"] = True
        _broadcast("speed_start", {})
        r = measure.run_speed_test()
        with _state_lock:
            _state["speed_running"] = False
        if r["success"]:
            last_dl = r["download_mbps"]
            last_ul = r["upload_mbps"]
            db.insert_speed(conn, r["ts"], r["server"],
                            r["download_mbps"], r["upload_mbps"], r["ping_ms"])
            if alerter:
                alerter.t = load_alerts()   # pick up any threshold changes from the UI
                alerter.check_speed(r.get("download_mbps"), r.get("upload_mbps"))
                _flush_alerts()
            payload = {**r, "ts": r["ts"].isoformat()}
            with _state_lock:
                _state["latest_speed"] = payload
            _broadcast("speed", payload)

    with _state_lock:
        _state["monitor_running"] = True

    do_check()
    if speed_interval > 0:
        do_speed()
        last_speed_time = time.time()

    while not _monitor_stop.is_set():
        _monitor_stop.wait(interval)
        if _monitor_stop.is_set():
            break
        do_check()
        if speed_interval > 0 and (time.time() - last_speed_time) >= speed_interval:
            do_speed()
            last_speed_time = time.time()

    with _state_lock:
        _state["monitor_running"] = False
    conn.close()


# ── Startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global _sse_loop, _monitor_thread
    logging.basicConfig(level=logging.INFO)
    _sse_loop = asyncio.get_event_loop()

    cfg = load_alerts()
    alerter = AlertManager(cfg)

    _monitor_stop.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=("8.8.8.8", 30, 5, 0, alerter),
        daemon=True,
    )
    _monitor_thread.start()


@app.on_event("shutdown")
async def shutdown():
    _monitor_stop.set()


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status")
def api_status():
    with _state_lock:
        return {**_state, "latest": _state["latest"],
                "latest_speed": _state["latest_speed"]}


@app.get("/api/checks")
def api_checks(hours: int = Query(24), host: Optional[str] = None, last: int = 0):
    conn = db.get_conn(DB_PATH)
    rows = db.fetch_checks(conn, hours, host)
    conn.close()
    return rows[-last:] if last else rows


@app.get("/api/speeds")
def api_speeds(hours: int = Query(24), last: int = 0):
    conn = db.get_conn(DB_PATH)
    rows = db.fetch_speeds(conn, hours)
    conn.close()
    return rows[-last:] if last else rows


@app.get("/api/summary")
def api_summary(hours: int = Query(24)):
    conn = db.get_conn(DB_PATH)
    s = db.fetch_summary(conn, hours)
    conn.close()
    return s


@app.post("/api/speed")
async def api_run_speed(background_tasks: BackgroundTasks):
    """Trigger an on-demand speed test."""
    with _state_lock:
        if _state["speed_running"]:
            raise HTTPException(409, "Speed test already running")
    def _run():
        with _state_lock:
            _state["speed_running"] = True
        _broadcast("speed_start", {})
        r = measure.run_speed_test()
        with _state_lock:
            _state["speed_running"] = False
        if r["success"]:
            conn = db.get_conn(DB_PATH)
            db.insert_speed(conn, r["ts"], r["server"],
                            r["download_mbps"], r["upload_mbps"], r["ping_ms"])
            conn.close()
            # Check alerts with freshly loaded thresholds
            am = AlertManager(load_alerts())
            am.check_speed(r.get("download_mbps"), r.get("upload_mbps"))
            _flush_alerts()
            payload = {**r, "ts": r["ts"].isoformat()}
            with _state_lock:
                _state["latest_speed"] = payload
            _broadcast("speed", payload)
    background_tasks.add_task(_run)
    return {"status": "started"}


# ── Alert config ──────────────────────────────────────────────────────────────

@app.get("/api/alerts")
def api_get_alerts():
    return load_alerts().to_dict()


class AlertsUpdate(BaseModel):
    latency_ms:    Optional[float] = None
    jitter_ms:     Optional[float] = None
    pkt_loss_pct:  Optional[float] = None
    download_mbps: Optional[float] = None
    upload_mbps:   Optional[float] = None
    cooldown_sec:  Optional[int]   = None
    ntfy_topic:    Optional[str]   = None
    ntfy_server:   Optional[str]   = None


@app.put("/api/alerts")
def api_set_alerts(body: AlertsUpdate):
    t = load_alerts()
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(t, field, None if val == 0 else val)
    save_alerts(t)
    return t.to_dict()


# ── NL query ─────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    hours: int = 24


@app.post("/api/query")
def api_query(body: QueryRequest):
    from netmon import query as q_mod
    conn = db.get_conn(DB_PATH)
    answer = q_mod.ask(body.question, conn, body.hours)
    conn.close()
    return {"answer": answer}


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.get("/api/stream")
async def api_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _sse_listeners.append(queue)

    async def generator():
        try:
            # Send current state immediately on connect
            with _state_lock:
                snap = dict(_state)
            yield {"event": "state", "data": json.dumps(snap)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield {"event": event, "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            _sse_listeners.remove(queue)

    return EventSourceResponse(generator())
