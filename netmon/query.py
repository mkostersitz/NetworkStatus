"""Natural-language querying of network history via Claude."""
import json
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from . import db

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


SYSTEM_PROMPT = """\
You are a network performance analyst. You are given structured JSON data from a network
monitoring tool and answer the user's natural-language question precisely and helpfully.

Data fields:
  checks: [{ts, host, latency_ms, jitter_ms, pkt_loss, success}]
  speeds: [{ts, server, download_mbps, upload_mbps, ping_ms}]
  summary.checks: {total, failures, avg_latency, min_latency, max_latency, avg_jitter, avg_loss}
  summary.speeds: {total, avg_dl, max_dl, min_dl, avg_ul, max_ul, min_ul, avg_ping}

Units: latency/jitter/ping in ms, pkt_loss in %, download/upload in Mbps.
Consistency = (1 - failures/total) * 100%.
Provide clear, concise answers. Include numbers with units. Highlight problems in plain language.
If the question requires data not present, say so."""


def _build_context(conn, hours: int = 24) -> dict:
    summary = db.fetch_summary(conn, hours)
    checks  = db.fetch_checks(conn, hours)
    speeds  = db.fetch_speeds(conn, hours)

    # Thin the checks list if very large (keep last 200 + first 10 for context)
    if len(checks) > 210:
        checks = checks[:10] + checks[-200:]

    return {
        "window_hours": hours,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "checks": checks,
        "speeds": speeds,
    }


def ask(
    question: str,
    conn,
    hours: int = 24,
    model: str = "claude-sonnet-4-6",
    api_key: Optional[str] = None,
) -> str:
    if not _HAS_ANTHROPIC:
        return "[error] anthropic package not installed. Run: pip install anthropic"

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return (
            "[error] ANTHROPIC_API_KEY not set.\n"
            "Export it: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if not workspace_id:
        return (
            "[error] ANTHROPIC_WORKSPACE_ID not set. "
            "This API key requires a workspace ID.\n"
            "Add it to the service: sudo systemctl edit netmon\n"
            "Then add: Environment=ANTHROPIC_WORKSPACE_ID=wrkspc-..."
        )
    client = anthropic.Anthropic(
        api_key=key,
        default_headers={"anthropic-workspace-id": workspace_id},
    )
    context = _build_context(conn, hours)
    data_json = json.dumps(context, indent=2, default=str)

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Network data (last {hours}h):\n```json\n{data_json}\n```\n\n"
                    f"Question: {question}"
                ),
            }
        ],
    )
    return message.content[0].text


def interactive_session(conn, hours: int = 24, model: str = "claude-sonnet-4-6"):
    """REPL for natural-language queries."""
    from rich.console import Console
    from rich.markdown import Markdown

    c = Console()
    c.print(
        f"[bold cyan]NetMon NL Query[/] — asking about the last [yellow]{hours}h[/]\n"
        "Type your question (or [dim]quit[/] to exit)\n"
    )

    while True:
        try:
            q = input("▶ ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"quit", "exit", "q"}:
            break
        with c.status("Thinking…"):
            answer = ask(q, conn, hours, model)
        c.print(Markdown(answer))
        c.print()
