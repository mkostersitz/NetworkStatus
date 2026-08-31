#!/usr/bin/env python3
"""
NetMon CLI — network monitoring, reporting, and natural-language querying.

Usage examples:
  ./netmon monitor                          # latency check every 30s
  ./netmon monitor --interval 60 --speed-interval 3600
  ./netmon speed                            # one-off speed test
  ./netmon ping --host 1.1.1.1             # one-off ping
  ./netmon report summary
  ./netmon report table checks --hours 12
  ./netmon report table speeds
  ./netmon report graph latency --hours 6
  ./netmon report graph all
  ./netmon query "Was my network reliable last night?"
  ./netmon query --interactive
"""
import sys
from pathlib import Path

import click
from rich.console import Console

from netmon import db, measure, monitor, report, query
from netmon.alerts import AlertManager, Thresholds, load as load_alerts, save as save_alerts, DEFAULT_CFG as ALERTS_CFG

console = Console()


def _conn(db_path):
    return db.get_conn(Path(db_path) if db_path else db.DEFAULT_DB)


# ── Root group ───────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.0.0", prog_name="netmon")
def cli():
    """NetMon — monitor upload/download speed, latency, jitter & consistency."""


# ── monitor ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--host",            default="8.8.8.8",  show_default=True,
              help="Host to ping for latency checks.")
@click.option("--interval",        default=30,         show_default=True, type=int,
              help="Seconds between latency checks.")
@click.option("--ping-count",      default=5,          show_default=True, type=int,
              help="ICMP packets per latency check.")
@click.option("--speed-interval",  default=0,          show_default=True, type=int,
              help="Seconds between auto speed tests (0=disabled).")
@click.option("--db",              "db_path", default=None,
              help="Path to SQLite DB (default: ~/.netmon/history.db).")
@click.option("--quiet/--no-quiet",default=False,
              help="Suppress per-check console output.")
@click.option("--no-alerts",       is_flag=True,
              help="Disable macOS notifications for this session.")
@click.option("--alerts-config",   "alerts_cfg", default=None, metavar="FILE",
              help="Path to an alert config JSON (default: ~/.netmon/alerts.json).")
def monitor_cmd(host, interval, ping_count, speed_interval, db_path, quiet, no_alerts, alerts_cfg):
    """Continuously monitor latency, jitter, and optionally run speed tests."""
    cfg_path = Path(alerts_cfg) if alerts_cfg else ALERTS_CFG
    thresholds = None if no_alerts else load_alerts(cfg_path)
    if not no_alerts:
        t = thresholds
        console.print(
            f"[dim]Alerts: latency>{t.latency_ms}ms  "
            f"jitter>{t.jitter_ms}ms  "
            f"loss>{t.pkt_loss_pct}%"
            + (f"  dl<{t.download_mbps}Mbps" if t.download_mbps else "")
            + (f"  ul<{t.upload_mbps}Mbps"   if t.upload_mbps   else "")
            + f"  (cooldown {t.cooldown_sec}s)[/]"
        )
    monitor.run(
        host=host,
        interval=interval,
        ping_count=ping_count,
        speed_interval=speed_interval,
        db_path=Path(db_path) if db_path else None,
        quiet=quiet,
        alert_thresholds=thresholds,
        no_alerts=no_alerts,
    )


# ── speed ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--db", "db_path", default=None)
@click.option("--save/--no-save", default=True, help="Save result to DB.")
@click.option("--http-only", is_flag=True,
              help="Use HTTP estimate instead of speedtest.net (faster, less accurate).")
def speed(db_path, save, http_only):
    """Run a one-off speed test."""
    if http_only:
        console.print("[yellow]Running HTTP download estimate…[/]")
        r = measure.http_speed_estimate()
        if r["success"]:
            console.print(f"[green]Download estimate:[/] {r['download_mbps']:.1f} Mbps")
        else:
            console.print(f"[red]Failed:[/] {r.get('error')}")
        return

    console.print("[yellow]Running full speed test (this takes ~20s)…[/]")
    r = measure.run_speed_test()
    if r["success"]:
        ping_str = f"\n[cyan]Ping:[/]       {r['ping_ms']:.0f} ms" if r["ping_ms"] else ""
        console.print(
            f"[green]↓ Download:[/] {r['download_mbps']:.1f} Mbps\n"
            f"[blue]↑ Upload:[/]   {r['upload_mbps']:.1f} Mbps"
            f"{ping_str}\n"
            f"[dim]Server: {r['server']}[/]"
        )
        if save:
            conn = _conn(db_path)
            db.insert_speed(conn, r["ts"], r["server"],
                            r["download_mbps"], r["upload_mbps"], r["ping_ms"])
            conn.close()
            console.print("[dim]Saved to DB.[/]")
    else:
        console.print(f"[red]Speed test failed:[/] {r.get('error')}")


# ── ping ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--host",    default="8.8.8.8", show_default=True)
@click.option("--count",   default=10,         show_default=True, type=int)
@click.option("--db",      "db_path", default=None)
@click.option("--save/--no-save", default=True)
def ping(host, count, db_path, save):
    """Run a one-off latency / jitter check."""
    r = measure.measure_latency(host, count)
    if r["success"]:
        console.print(
            f"[cyan]{host}[/]\n"
            f"  Latency:  [green]{r['latency_ms']:.1f} ms[/]\n"
            f"  Jitter:   {r['jitter_ms']:.1f} ms\n"
            f"  Pkt loss: {r['pkt_loss']:.1f}%"
        )
    else:
        console.print(f"[red]Failed to reach {host}[/]")

    if save:
        conn = _conn(db_path)
        db.insert_check(conn, r["ts"], host, r.get("latency_ms"),
                        r.get("jitter_ms"), r.get("pkt_loss", 100), r["success"])
        conn.close()
        console.print("[dim]Saved to DB.[/]")


# ── report ────────────────────────────────────────────────────────────────────

@cli.group()
def report_cmd():
    """Display reports: tables, graphs, summaries."""


@report_cmd.command("summary")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--db", "db_path", default=None)
def summary(hours, db_path):
    """Print a summary of network performance."""
    conn = _conn(db_path)
    report.table_summary(conn, hours)
    conn.close()


@report_cmd.group("table")
def table_cmd():
    """Show data as a table."""


@table_cmd.command("checks")
@click.option("--hours",  default=24,  show_default=True, type=int)
@click.option("--host",   default=None)
@click.option("--last",   default=0,   type=int, help="Show last N rows only.")
@click.option("--db",     "db_path", default=None)
def table_checks(hours, host, last, db_path):
    conn = _conn(db_path)
    report.table_checks(conn, hours, host, last)
    conn.close()


@table_cmd.command("speeds")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--last",  default=0,  type=int)
@click.option("--db",    "db_path", default=None)
def table_speeds(hours, last, db_path):
    conn = _conn(db_path)
    report.table_speeds(conn, hours, last)
    conn.close()


@report_cmd.group("graph")
def graph_cmd():
    """Show data as a terminal graph."""


@graph_cmd.command("latency")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--host",  default=None)
@click.option("--db",    "db_path", default=None)
def graph_latency(hours, host, db_path):
    conn = _conn(db_path)
    report.graph_latency(conn, hours, host)
    conn.close()


@graph_cmd.command("speed")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--db",    "db_path", default=None)
def graph_speed(hours, db_path):
    conn = _conn(db_path)
    report.graph_speed(conn, hours)
    conn.close()


@graph_cmd.command("loss")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--db",    "db_path", default=None)
def graph_loss(hours, db_path):
    conn = _conn(db_path)
    report.graph_loss(conn, hours)
    conn.close()


@graph_cmd.command("all")
@click.option("--hours", default=24, show_default=True, type=int)
@click.option("--db",    "db_path", default=None)
def graph_all(hours, db_path):
    conn = _conn(db_path)
    report.graph_all(conn, hours)
    conn.close()


# ── query ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("question", nargs=-1)
@click.option("--interactive", "-i", is_flag=True,
              help="Start an interactive query session.")
@click.option("--hours",  default=24,  show_default=True, type=int,
              help="Hours of history to include.")
@click.option("--model",  default="claude-sonnet-4-6", show_default=True)
@click.option("--db",     "db_path", default=None)
def query_cmd(question, interactive, hours, model, db_path):
    """Ask a natural-language question about your network history."""
    conn = _conn(db_path)
    if interactive:
        query.interactive_session(conn, hours, model)
    elif question:
        q = " ".join(question)
        with console.status("Querying…"):
            answer = query.ask(q, conn, hours, model)
        from rich.markdown import Markdown
        console.print(Markdown(answer))
    else:
        console.print("[red]Provide a question or use --interactive.[/]")
    conn.close()


# ── alerts ───────────────────────────────────────────────────────────────────

@cli.group("alerts")
def alerts_cmd():
    """View and configure alert thresholds for macOS notifications."""


_ALERTS_CFG_OPT = click.option(
    "--config", "-c", "alerts_cfg", default=None, metavar="FILE",
    help="Alert config JSON to use (default: ~/.netmon/alerts.json).",
)


def _acfg(alerts_cfg):
    return Path(alerts_cfg) if alerts_cfg else ALERTS_CFG


@alerts_cmd.command("show")
@_ALERTS_CFG_OPT
def alerts_show(alerts_cfg):
    """Show current alert thresholds."""
    from rich.table import Table
    from rich import box
    cfg_path = _acfg(alerts_cfg)
    t = load_alerts(cfg_path)
    tbl = Table(title=f"Alert thresholds  ({cfg_path})", box=box.ROUNDED)
    tbl.add_column("Metric",    style="bold")
    tbl.add_column("Threshold", justify="right")
    tbl.add_column("Direction")

    def row(label, val, direction, unit):
        v = f"{val} {unit}" if val is not None else "[dim]disabled[/]"
        tbl.add_row(label, v, direction)

    row("Latency",    t.latency_ms,    "alert if ABOVE", "ms")
    row("Jitter",     t.jitter_ms,     "alert if ABOVE", "ms")
    row("Packet loss",t.pkt_loss_pct,  "alert if ABOVE", "%")
    row("Download",   t.download_mbps, "alert if BELOW", "Mbps")
    row("Upload",     t.upload_mbps,   "alert if BELOW", "Mbps")
    tbl.add_section()
    tbl.add_row("Cooldown", f"{t.cooldown_sec} s", "between repeated alerts")
    console.print(tbl)


@alerts_cmd.command("set")
@click.option("--latency",   type=float, default=None,
              help="Alert if latency exceeds N ms  (0 to disable).")
@click.option("--jitter",    type=float, default=None,
              help="Alert if jitter exceeds N ms   (0 to disable).")
@click.option("--loss",      type=float, default=None,
              help="Alert if packet loss exceeds N%  (0 to disable).")
@click.option("--download",  type=float, default=None,
              help="Alert if download drops below N Mbps  (0 to disable).")
@click.option("--upload",    type=float, default=None,
              help="Alert if upload drops below N Mbps    (0 to disable).")
@click.option("--cooldown",  type=int,   default=None,
              help="Seconds between repeated alerts for the same metric.")
@_ALERTS_CFG_OPT
def alerts_set(latency, jitter, loss, download, upload, cooldown, alerts_cfg):
    """Update one or more alert thresholds."""
    cfg_path = _acfg(alerts_cfg)
    t = load_alerts(cfg_path)
    changed = []

    def _apply(attr, val, label):
        if val is not None:
            setattr(t, attr, None if val == 0 else val)
            changed.append(f"{label} → {getattr(t, attr)}")

    _apply("latency_ms",    latency,  "latency")
    _apply("jitter_ms",     jitter,   "jitter")
    _apply("pkt_loss_pct",  loss,     "packet loss")
    _apply("download_mbps", download, "download")
    _apply("upload_mbps",   upload,   "upload")
    _apply("cooldown_sec",  cooldown, "cooldown")

    if not changed:
        console.print("[yellow]Nothing changed. Pass at least one option.[/]")
        return

    save_alerts(t, cfg_path)
    for c in changed:
        console.print(f"[green]✓[/] {c}")
    console.print(f"[dim]Saved to {cfg_path}[/]")


@alerts_cmd.command("test")
@_ALERTS_CFG_OPT
def alerts_test(alerts_cfg):
    """Fire a test notification to verify alerts are working."""
    from netmon.alerts import _notify
    _notify("NetMon — Test Alert", "Alerts are working correctly ✓")
    console.print("[green]Test notification sent.[/] Check your notification centre.")


@alerts_cmd.command("init")
@click.argument("file", type=click.Path())
@click.option("--from", "from_cfg", default=None, metavar="FILE",
              help="Copy from an existing config instead of defaults.")
def alerts_init(file, from_cfg):
    """Create a new alert config FILE (defaults or copy of another)."""
    dest = Path(file)
    if dest.exists():
        console.print(f"[red]Already exists:[/] {dest}  (use 'alerts set --config' to modify it)")
        return
    source = load_alerts(Path(from_cfg)) if from_cfg else Thresholds()
    save_alerts(source, dest)
    console.print(f"[green]Created:[/] {dest}")


@alerts_cmd.command("reset")
@_ALERTS_CFG_OPT
def alerts_reset(alerts_cfg):
    """Reset all thresholds to defaults."""
    cfg_path = _acfg(alerts_cfg)
    save_alerts(Thresholds(), cfg_path)
    console.print(f"[green]Reset to defaults.[/]  ({cfg_path})")


# ── alias ─────────────────────────────────────────────────────────────────────
cli.add_command(monitor_cmd,  name="monitor")
cli.add_command(report_cmd,   name="report")
cli.add_command(query_cmd,    name="query")
cli.add_command(alerts_cmd,   name="alerts")


if __name__ == "__main__":
    cli()
