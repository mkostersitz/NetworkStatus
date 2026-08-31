"""Reporting: rich tables and terminal/PNG graphs."""
from __future__ import annotations
from typing import Optional
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich import box
import plotext as plt

from . import db

console = Console()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ts(s: str) -> str:
    try:
        return datetime.fromisoformat(s).strftime("%m-%d %H:%M")
    except Exception:
        return s


def _f(v, decimals=1) -> str:
    return f"{v:.{decimals}f}" if v is not None else "—"


def _color(v, good, warn) -> str:
    """Return a rich color string based on thresholds (lower is better)."""
    if v is None:
        return "dim"
    if v <= good:
        return "green"
    if v <= warn:
        return "yellow"
    return "red"


# ── Table output ─────────────────────────────────────────────────────────────

def table_checks(conn, hours: int = 24, host: Optional[str] = None, last_n: int = 0):
    rows = db.fetch_checks(conn, hours, host)
    if last_n:
        rows = rows[-last_n:]

    t = Table(
        title=f"Latency checks — last {hours}h",
        box=box.ROUNDED,
        show_lines=False,
    )
    t.add_column("Time",    style="dim",    no_wrap=True)
    t.add_column("Host",    style="cyan")
    t.add_column("Latency", justify="right")
    t.add_column("Jitter",  justify="right")
    t.add_column("Loss %",  justify="right")
    t.add_column("OK",      justify="center")

    for r in rows:
        lat_c = _color(r["latency_ms"], 50, 150)
        jit_c = _color(r["jitter_ms"],  10, 30)
        los_c = _color(r["pkt_loss"],   1,  5)
        ok    = "[green]✓[/]" if r["success"] else "[red]✗[/]"
        t.add_row(
            _ts(r["ts"]),
            r["host"],
            f"[{lat_c}]{_f(r['latency_ms'])} ms[/]",
            f"[{jit_c}]{_f(r['jitter_ms'])} ms[/]",
            f"[{los_c}]{_f(r['pkt_loss'])}[/]",
            ok,
        )
    console.print(t)


def table_speeds(conn, hours: int = 24, last_n: int = 0):
    rows = db.fetch_speeds(conn, hours)
    if last_n:
        rows = rows[-last_n:]

    t = Table(
        title=f"Speed tests — last {hours}h",
        box=box.ROUNDED,
    )
    t.add_column("Time",   style="dim", no_wrap=True)
    t.add_column("Server", style="cyan")
    t.add_column("↓ Mbps", justify="right")
    t.add_column("↑ Mbps", justify="right")
    t.add_column("Ping",   justify="right")

    for r in rows:
        t.add_row(
            _ts(r["ts"]),
            r.get("server") or "—",
            f"[green]{_f(r['download_mbps'])}[/]",
            f"[blue]{_f(r['upload_mbps'])}[/]",
            f"{_f(r['ping_ms'])} ms",
        )
    console.print(t)


def table_summary(conn, hours: int = 24):
    s = db.fetch_summary(conn, hours)
    c = s["checks"]
    sp = s["speeds"]

    t = Table(title=f"Summary — last {hours}h", box=box.SIMPLE_HEAVY)
    t.add_column("Metric", style="bold")
    t.add_column("Value",  justify="right")

    total   = c.get("total") or 0
    fails   = c.get("failures") or 0
    uptime  = (1 - fails / total) * 100 if total else 0

    t.add_row("Checks run",     str(total))
    t.add_row("Failures",       f"[red]{fails}[/]")
    t.add_row("Consistency",    f"[{'green' if uptime>=99 else 'yellow' if uptime>=95 else 'red'}]{uptime:.1f}%[/]")
    t.add_row("Avg latency",    f"{_f(c.get('avg_latency'))} ms")
    t.add_row("Min / Max lat",  f"{_f(c.get('min_latency'))} / {_f(c.get('max_latency'))} ms")
    t.add_row("Avg jitter",     f"{_f(c.get('avg_jitter'))} ms")
    t.add_row("Avg packet loss",f"{_f(c.get('avg_loss'))}%")
    t.add_section()
    t.add_row("Speed tests",    str(sp.get("total") or 0))
    t.add_row("Avg download",   f"{_f(sp.get('avg_dl'))} Mbps")
    t.add_row("Avg upload",     f"{_f(sp.get('avg_ul'))} Mbps")
    t.add_row("Best download",  f"{_f(sp.get('max_dl'))} Mbps")
    t.add_row("Best upload",    f"{_f(sp.get('max_ul'))} Mbps")

    console.print(t)


# ── Graph output (terminal) ──────────────────────────────────────────────────

def graph_latency(conn, hours: int = 24, host: Optional[str] = None):
    rows = db.fetch_checks(conn, hours, host)
    if not rows:
        console.print("[yellow]No data.[/]")
        return

    xs = list(range(len(rows)))
    lats  = [r["latency_ms"] or 0 for r in rows]
    jits  = [r["jitter_ms"]  or 0 for r in rows]
    labels = [_ts(r["ts"]) for r in rows]

    plt.clear_figure()
    plt.theme("dark")
    plt.plot(xs, lats, label="Latency (ms)")
    plt.plot(xs, jits, label="Jitter (ms)")
    plt.title(f"Latency & Jitter — last {hours}h")
    plt.xlabel("Sample")
    plt.ylabel("ms")
    plt.plot_size(100, 28)
    plt.show()


def graph_speed(conn, hours: int = 24):
    rows = db.fetch_speeds(conn, hours)
    if not rows:
        console.print("[yellow]No speed test data.[/]")
        return

    xs = list(range(len(rows)))
    dl = [r["download_mbps"] or 0 for r in rows]
    ul = [r["upload_mbps"]   or 0 for r in rows]

    plt.clear_figure()
    plt.theme("dark")
    plt.bar(xs, dl, label="Download (Mbps)", width=0.4)
    plt.bar([x + 0.4 for x in xs], ul, label="Upload (Mbps)", width=0.4)
    plt.title(f"Speed Tests — last {hours}h")
    plt.ylabel("Mbps")
    plt.plot_size(100, 22)
    plt.show()


def graph_loss(conn, hours: int = 24):
    rows = db.fetch_checks(conn, hours)
    if not rows:
        console.print("[yellow]No data.[/]")
        return

    xs   = list(range(len(rows)))
    loss = [r["pkt_loss"] or 0 for r in rows]

    plt.clear_figure()
    plt.theme("dark")
    plt.bar(xs, loss, label="Packet Loss %")
    plt.title(f"Packet Loss — last {hours}h")
    plt.ylabel("%")
    plt.plot_size(100, 18)
    plt.show()


def graph_all(conn, hours: int = 24):
    graph_latency(conn, hours)
    graph_speed(conn, hours)
    graph_loss(conn, hours)
