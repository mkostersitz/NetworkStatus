"""Core network measurement functions."""
import subprocess
import re
import time
import socket
import statistics
import threading
from datetime import datetime, timezone
from typing import Optional

import requests


# ── Latency / Jitter / Packet-loss ─────────────────────────────────────────

def ping(host: str = "8.8.8.8", count: int = 10, timeout: int = 5) -> dict:
    """
    Run system ping and parse results.
    Returns latency_ms, jitter_ms, pkt_loss (0-100), success.
    """
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout * 1000), host],
            capture_output=True, text=True, timeout=timeout + count + 5
        )
        out = result.stdout

        # Extract round-trip times
        rtt_line = re.search(r"round-trip.*?=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out)
        loss_line = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)

        pkt_loss = float(loss_line.group(1)) if loss_line else 100.0

        if rtt_line:
            min_ms, avg_ms, max_ms, stddev_ms = map(float, rtt_line.groups())
            return {
                "latency_ms": avg_ms,
                "jitter_ms": stddev_ms,
                "pkt_loss": pkt_loss,
                "success": True,
            }
        else:
            # Parse individual RTTs for manual jitter calc
            rtts = list(map(float, re.findall(r"time=([\d.]+)\s*ms", out)))
            if rtts:
                return {
                    "latency_ms": statistics.mean(rtts),
                    "jitter_ms": statistics.stdev(rtts) if len(rtts) > 1 else 0.0,
                    "pkt_loss": pkt_loss,
                    "success": True,
                }
            return {"latency_ms": None, "jitter_ms": None,
                    "pkt_loss": 100.0, "success": False}

    except Exception:
        return {"latency_ms": None, "jitter_ms": None,
                "pkt_loss": 100.0, "success": False}


def tcp_latency(host: str = "8.8.8.8", port: int = 53,
                count: int = 5) -> dict:
    """Fallback latency check using TCP connect time (no root needed)."""
    rtts = []
    for _ in range(count):
        try:
            t0 = time.perf_counter()
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            rtts.append((time.perf_counter() - t0) * 1000)
        except Exception:
            pass
        time.sleep(0.1)

    if not rtts:
        return {"latency_ms": None, "jitter_ms": None,
                "pkt_loss": 100.0, "success": False}

    loss = (count - len(rtts)) / count * 100
    return {
        "latency_ms": statistics.mean(rtts),
        "jitter_ms": statistics.stdev(rtts) if len(rtts) > 1 else 0.0,
        "pkt_loss": loss,
        "success": True,
    }


def measure_latency(host: str = "8.8.8.8", count: int = 10) -> dict:
    """Try ping first, fall back to TCP if ICMP is blocked."""
    result = ping(host, count)
    if not result["success"]:
        result = tcp_latency(host, count=count)
    result["host"] = host
    result["ts"] = datetime.now(timezone.utc)
    return result


# ── Fast.com speed test ──────────────────────────────────────────────────────

_FAST_JS_URL  = "https://fast.com/app-{}.js"
_FAST_API_URL = "https://api.fast.com/netflix/speedtest/v2"
_FAST_TOKEN_CACHE: dict = {}   # {token, expires}

_DL_DURATION  = 10   # seconds to stream download
_UL_DURATION  = 8    # seconds to stream upload
_UL_CHUNK     = 256 * 1024  # 256 KB upload chunks


_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _get_fast_token() -> str:
    """Scrape the current API token from the fast.com JS bundle."""
    import re as _re
    html = requests.get("https://fast.com", headers=_HEADERS, timeout=10).text
    js_file = _re.search(r'app-([a-f0-9]+)\.js', html)
    if not js_file:
        raise RuntimeError("Could not find fast.com JS bundle")
    js = requests.get(
        f"https://fast.com/app-{js_file.group(1)}.js", headers=_HEADERS, timeout=10
    ).text
    token = _re.search(r'token:"([^"]+)"', js)
    if not token:
        raise RuntimeError("Could not find fast.com token in JS bundle")
    return token.group(1)


def _fast_targets(token: str, count: int = 5) -> list[dict]:
    r = requests.get(
        _FAST_API_URL,
        params={"https": "true", "token": token, "urlCount": count},
        headers=_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["targets"]


def _measure_ping_fast(urls: list[str], count: int = 3) -> float:
    """Measure latency to fast.com targets by timing a 1-byte range request."""
    rtts = []
    for url in urls:
        for _ in range(count):
            try:
                t0 = time.perf_counter()
                requests.get(url, headers={**_HEADERS, "Range": "bytes=0-0"},
                             timeout=5, stream=False)
                rtts.append((time.perf_counter() - t0) * 1000)
            except Exception:
                pass
    return statistics.median(rtts) if rtts else None


def _measure_download(url: str, duration: float) -> float:
    """Return bytes downloaded from url over `duration` seconds."""
    deadline = time.perf_counter() + duration
    downloaded = 0
    with requests.get(url, stream=True, timeout=duration + 5) as r:
        r.raise_for_status()
        for chunk in r.iter_content(65536):
            downloaded += len(chunk)
            if time.perf_counter() >= deadline:
                break
    return downloaded


def _measure_upload(url: str, duration: float) -> float:
    """Return bytes uploaded to url over `duration` seconds."""
    deadline = time.perf_counter() + duration
    uploaded = 0
    chunk = b"0" * _UL_CHUNK

    def gen():
        nonlocal uploaded
        while time.perf_counter() < deadline:
            yield chunk
            uploaded += len(chunk)

    try:
        requests.post(url, data=gen(), timeout=duration + 5,
                      headers={"Content-Type": "application/octet-stream"})
    except Exception:
        pass
    return uploaded


def run_speed_test(url_count: int = 5) -> dict:
    """
    Measure download and upload speed using Netflix's fast.com infrastructure.
    Downloads/uploads run concurrently across multiple CDN targets.
    """
    import concurrent.futures as _cf

    try:
        token   = _get_fast_token()
        targets = _fast_targets(token, url_count)
        urls    = [t["url"] for t in targets]
        server  = ", ".join(
            f"{t['location']['city']}" for t in targets[:2]
        ) + " (fast.com)"

        # ── Ping ─────────────────────────────────────────────────────────
        ping_ms = _measure_ping_fast(urls)

        # ── Download ──────────────────────────────────────────────────────
        t0 = time.perf_counter()
        with _cf.ThreadPoolExecutor(max_workers=len(urls)) as ex:
            futures = [ex.submit(_measure_download, u, _DL_DURATION) for u in urls]
            total_dl = sum(f.result() for f in _cf.as_completed(futures))
        dl_elapsed = time.perf_counter() - t0
        download_mbps = (total_dl * 8) / (dl_elapsed * 1e6)

        # ── Upload ────────────────────────────────────────────────────────
        t1 = time.perf_counter()
        with _cf.ThreadPoolExecutor(max_workers=len(urls)) as ex:
            futures = [ex.submit(_measure_upload, u, _UL_DURATION) for u in urls]
            total_ul = sum(f.result() for f in _cf.as_completed(futures))
        ul_elapsed = time.perf_counter() - t1
        upload_mbps = (total_ul * 8) / (ul_elapsed * 1e6)

        return {
            "download_mbps": download_mbps,
            "upload_mbps":   upload_mbps,
            "ping_ms":       ping_ms,
            "server":        server,
            "ts":            datetime.now(timezone.utc),
            "success":       True,
        }

    except Exception as e:
        return {
            "download_mbps": None, "upload_mbps": None,
            "ping_ms": None, "server": None,
            "ts": datetime.now(timezone.utc),
            "success": False, "error": str(e),
        }


# ── HTTP-based fallback speed estimate ──────────────────────────────────────

def http_speed_estimate(size_mb: int = 10) -> dict:
    """Quick download speed estimate using a public file (no speedtest server needed)."""
    url = f"https://speed.cloudflare.com/__down?bytes={size_mb * 1024 * 1024}"
    try:
        t0 = time.perf_counter()
        r = requests.get(url, timeout=30, stream=True)
        downloaded = 0
        for chunk in r.iter_content(65536):
            downloaded += len(chunk)
        elapsed = time.perf_counter() - t0
        mbps = (downloaded * 8) / (elapsed * 1e6)
        return {"download_mbps": mbps, "success": True,
                "ts": datetime.now(timezone.utc)}
    except Exception as e:
        return {"download_mbps": None, "success": False,
                "error": str(e), "ts": datetime.now(timezone.utc)}
