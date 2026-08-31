"""Threshold alerting — cross-platform (macOS, ntfy.sh, in-browser queue)."""
from __future__ import annotations
import collections
import logging
import platform
import subprocess
import time
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("netmon.alerts")

DEFAULT_CFG = Path.home() / ".netmon" / "alerts.json"
_COOLDOWN = 300  # 5 minutes between repeated alerts for the same metric

# In-process queue consumed by the web SSE stream (/api/stream).
# Capped so it never grows unbounded if nothing is reading.
alert_queue: collections.deque = collections.deque(maxlen=50)


@dataclass
class Thresholds:
    latency_ms:    Optional[float] = 150.0
    jitter_ms:     Optional[float] = 30.0
    pkt_loss_pct:  Optional[float] = 5.0
    download_mbps: Optional[float] = None   # alert if BELOW this
    upload_mbps:   Optional[float] = None   # alert if BELOW this
    cooldown_sec:  int             = _COOLDOWN
    # ntfy.sh — set topic to enable push notifications
    ntfy_topic:    Optional[str]   = None
    ntfy_server:   str             = "https://ntfy.sh"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Thresholds":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def load(path: Path = DEFAULT_CFG) -> Thresholds:
    if path.exists():
        try:
            return Thresholds.from_dict(json.loads(path.read_text()))
        except Exception:
            pass
    return Thresholds()


def save(t: Thresholds, path: Path = DEFAULT_CFG):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(t.to_dict(), indent=2))


# ── Notification backends ────────────────────────────────────────────────────

def _notify_macos(title: str, message: str):
    script = f'display notification "{message}" with title "{title}" sound name "Basso"'
    try:
        subprocess.run(["osascript", "-e", script],
                       check=False, capture_output=True, timeout=5)
    except Exception:
        pass


def _notify_linux(title: str, message: str):
    try:
        subprocess.run(["notify-send", title, message],
                       check=False, capture_output=True, timeout=5)
    except Exception:
        pass


def _notify_ntfy(title: str, message: str, topic: str, server: str):
    url = f"{server.rstrip('/')}/{topic}"
    try:
        import requests as _req
        r = _req.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high",
                "Tags": "warning",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=5,
        )
        if not r.ok:
            log.warning("ntfy POST to %s returned %s: %s", url, r.status_code, r.text[:200])
        else:
            log.info("ntfy alert sent to %s", url)
    except Exception as e:
        log.warning("ntfy POST to %s failed: %s", url, e)


def _notify(title: str, message: str, thresholds: Optional[Thresholds] = None):
    """Dispatch to all enabled backends."""
    # In-browser queue (always)
    alert_queue.append({"title": title, "message": message,
                        "ts": time.time()})

    # ntfy.sh push
    if thresholds and thresholds.ntfy_topic:
        log.info("Dispatching ntfy alert: topic=%s server=%s", thresholds.ntfy_topic, thresholds.ntfy_server)
        _notify_ntfy(title, message, thresholds.ntfy_topic, thresholds.ntfy_server)

    # OS desktop notification
    sys = platform.system()
    if sys == "Darwin":
        _notify_macos(title, message)
    elif sys == "Linux":
        _notify_linux(title, message)


# ── Alert manager ────────────────────────────────────────────────────────────

class AlertManager:
    """Checks measurements against thresholds and fires notifications with cooldown."""

    def __init__(self, thresholds: Optional[Thresholds] = None,
                 cfg_path: Path = DEFAULT_CFG):
        self.t = thresholds or load(cfg_path)
        self._last_fired: dict[str, float] = {}

    def _can_fire(self, key: str) -> bool:
        now = time.monotonic()
        if now - self._last_fired.get(key, 0) >= self.t.cooldown_sec:
            self._last_fired[key] = now
            return True
        return False

    def _fire(self, title: str, message: str):
        _notify(title, message, self.t)

    def check_latency(self, latency_ms: Optional[float], jitter_ms: Optional[float],
                      pkt_loss: Optional[float], host: str = ""):
        label = f" ({host})" if host else ""

        if latency_ms is not None and self.t.latency_ms is not None:
            if latency_ms > self.t.latency_ms and self._can_fire("latency"):
                self._fire("NetMon — High Latency",
                           f"Latency {latency_ms:.0f} ms > {self.t.latency_ms:.0f} ms{label}")

        if jitter_ms is not None and self.t.jitter_ms is not None:
            if jitter_ms > self.t.jitter_ms and self._can_fire("jitter"):
                self._fire("NetMon — High Jitter",
                           f"Jitter {jitter_ms:.0f} ms > {self.t.jitter_ms:.0f} ms{label}")

        if pkt_loss is not None and self.t.pkt_loss_pct is not None:
            if pkt_loss > self.t.pkt_loss_pct and self._can_fire("pkt_loss"):
                self._fire("NetMon — Packet Loss",
                           f"Packet loss {pkt_loss:.1f}% > {self.t.pkt_loss_pct:.1f}%{label}")

    def check_speed(self, download_mbps: Optional[float], upload_mbps: Optional[float]):
        if download_mbps is not None and self.t.download_mbps is not None:
            if download_mbps < self.t.download_mbps and self._can_fire("download"):
                self._fire("NetMon — Low Download Speed",
                           f"Download {download_mbps:.1f} Mbps < {self.t.download_mbps:.1f} Mbps")

        if upload_mbps is not None and self.t.upload_mbps is not None:
            if upload_mbps < self.t.upload_mbps and self._can_fire("upload"):
                self._fire("NetMon — Low Upload Speed",
                           f"Upload {upload_mbps:.1f} Mbps < {self.t.upload_mbps:.1f} Mbps")
