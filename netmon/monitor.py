"""Background monitoring daemon — checks on a configurable interval."""
import time
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table

from . import db, measure, alerts
from .alerts import AlertManager, Thresholds

console = Console()


def _status_table(last: dict) -> Table:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="bold cyan")
    t.add_column(style="white")

    def fmt(v, unit="", ok_thresh=None, warn_thresh=None):
        if v is None:
            return "[dim]—[/]"
        s = f"{v:.1f}{unit}"
        if ok_thresh and warn_thresh:
            if v <= ok_thresh:
                return f"[green]{s}[/]"
            elif v <= warn_thresh:
                return f"[yellow]{s}[/]"
            else:
                return f"[red]{s}[/]"
        return s

    t.add_row("Host",     last.get("host", "—"))
    t.add_row("Latency",  fmt(last.get("latency_ms"), " ms", 50, 150))
    t.add_row("Jitter",   fmt(last.get("jitter_ms"),  " ms", 10, 30))
    t.add_row("Pkt Loss", fmt(last.get("pkt_loss"),   "%",   0,  2))
    t.add_row("Checks",   str(last.get("check_count", 0)))
    t.add_row("Failures", str(last.get("fail_count",  0)))
    t.add_row("Last",     last.get("ts_str", "—"))
    return t


def run(
    host: str = "8.8.8.8",
    interval: int = 30,
    ping_count: int = 5,
    speed_interval: int = 0,   # 0 = never auto-run
    db_path: Optional[Path] = None,
    live_display: bool = True,
    quiet: bool = False,
    alert_thresholds: Optional[Thresholds] = None,
    no_alerts: bool = False,
):
    """
    Main monitoring loop. Runs until SIGINT / KeyboardInterrupt.

    host           — target host for latency checks
    interval       — seconds between latency checks
    ping_count     — ICMP packets per check
    speed_interval — seconds between auto speed tests (0 = disabled)
    """
    conn = db.get_conn(db_path or db.DEFAULT_DB)
    alerter = None if no_alerts else AlertManager(alert_thresholds)
    stop = threading.Event()
    state = {"check_count": 0, "fail_count": 0, "host": host, "ts_str": "—"}

    # Whether to show speed on every check line (true when thresholds are set)
    show_speed = bool(
        alerter and (alerter.t.download_mbps is not None or alerter.t.upload_mbps is not None)
    )
    if show_speed and speed_interval == 0:
        speed_interval = 3600
        console.print(
            "[dim]Speed thresholds are set — auto-enabling speed tests every 1h. "
            "Use --speed-interval to change.[/]"
        )
    last_dl: Optional[float] = None
    last_ul: Optional[float] = None

    def sig_handler(s, f):
        stop.set()
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    last_speed = 0.0

    def do_check():
        r = measure.measure_latency(host, count=ping_count)
        state["check_count"] += 1
        if not r["success"]:
            state["fail_count"] += 1
        state.update({
            "latency_ms": r.get("latency_ms"),
            "jitter_ms":  r.get("jitter_ms"),
            "pkt_loss":   r.get("pkt_loss"),
            "ts_str":     datetime.now().strftime("%H:%M:%S"),
        })
        db.insert_check(
            conn, r["ts"], host,
            r.get("latency_ms"), r.get("jitter_ms"),
            r.get("pkt_loss", 100.0), r["success"],
        )
        if alerter:
            alerter.check_latency(
                r.get("latency_ms"), r.get("jitter_ms"), r.get("pkt_loss"), host
            )
        if not quiet:
            lat  = f"{r['latency_ms']:.1f}ms" if r.get("latency_ms") else "FAIL"
            jit  = f"{r['jitter_ms']:.1f}ms"  if r.get("jitter_ms")  else "—"
            loss = f"{r['pkt_loss']:.0f}%"     if r.get("pkt_loss") is not None else "—"
            speed_str = ""
            if show_speed:
                dl = f"[green]↓{last_dl:.1f}[/]" if last_dl is not None else "[dim]↓—[/]"
                ul = f"[blue]↑{last_ul:.1f}[/]"  if last_ul is not None else "[dim]↑—[/]"
                speed_str = f"  {dl}/{ul} Mbps"
            console.log(
                f"[cyan]{host}[/]  lat=[bold]{lat}[/]  "
                f"jitter={jit}  loss={loss}{speed_str}"
            )

    def do_speed():
        nonlocal last_dl, last_ul
        if not quiet:
            console.log("[yellow]Running speed test…[/]")
        r = measure.run_speed_test()
        if r["success"]:
            last_dl = r["download_mbps"]
            last_ul = r["upload_mbps"]
            db.insert_speed(conn, r["ts"], r["server"],
                            r["download_mbps"], r["upload_mbps"], r["ping_ms"])
            if alerter:
                alerter.check_speed(r.get("download_mbps"), r.get("upload_mbps"))
            if not quiet:
                ping_str = f"  ping={r['ping_ms']:.0f}ms" if r["ping_ms"] else ""
                console.log(
                    f"[green]Speed:[/] ↓{r['download_mbps']:.1f} Mbps  "
                    f"↑{r['upload_mbps']:.1f} Mbps{ping_str}"
                )
        else:
            if not quiet:
                console.log(f"[red]Speed test failed:[/] {r.get('error')}")

    console.print(
        f"[bold]NetMon[/] — monitoring [cyan]{host}[/] "
        f"every [yellow]{interval}s[/]  (Ctrl-C to stop)"
    )

    now = time.time()
    do_check()
    if speed_interval > 0:
        do_speed()
        last_speed = time.time()

    while not stop.is_set():
        stop.wait(interval)
        if stop.is_set():
            break
        do_check()
        if speed_interval > 0 and (time.time() - last_speed) >= speed_interval:
            do_speed()
            last_speed = time.time()

    console.print("[dim]Monitor stopped.[/]")
    conn.close()
