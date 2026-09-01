# NetMon — Network Monitoring Toolkit

Continuous monitoring of **latency, jitter, packet loss, and upload/download speed** with SQLite history, a real-time web UI, push alerts, and natural-language querying via Claude. Runs on macOS, Linux, Windows 11, and Raspberry Pi.

---

## Quick start

### macOS / Linux

```bash
cd NetworkStatus
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Web UI + monitor (recommended)
./serve.sh                        # open http://localhost:8080

# Terminal-only monitor
./netmon.sh monitor
```

### Windows 11 (PowerShell)

```powershell
cd NetworkStatus
py -3.12 -m venv .venv            # use py launcher, not python (avoids MSYS2/UCRT64 python)
.venv\Scripts\pip install -r requirements.txt

# Web UI + monitor (recommended)
.\serve.ps1                       # open http://localhost:8080

# Terminal-only monitor
.\netmon.ps1 monitor
```

> **Windows note:** Python must be installed via `winget install Python.Python.3.12` (or the python.org installer). MSYS2/UCRT64's `python` does not create a standard venv layout and will fail to build native extensions.

---

## Running as a background service

### Linux — systemd

A `netmon.service` unit file is included. Deploy to a Pi or any Linux host:

```bash
./deploy.sh mikek@raspberrypi.local
```

The service starts automatically on boot. Secrets (API keys) go in the systemd override file — **never** the main service file, which gets overwritten on deploy:

```bash
sudo systemctl edit netmon
```

Add:

```ini
[Service]
Environment=ANTHROPIC_API_KEY=sk-ant-...
Environment=ANTHROPIC_WORKSPACE_ID=wrkspc-...
Environment=NETMON_NTFY_TOPIC=your-topic
```

### Windows — Task Scheduler (runs at login, no extra tools)

Run once in PowerShell as Administrator:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-WindowStyle Hidden -File C:\data\repos\NetworkStatus\serve.ps1"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "NetMon" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest

# Start immediately without rebooting
Start-ScheduledTask -TaskName "NetMon"
```

### Windows — NSSM (Windows service, runs without login)

```powershell
winget install NSSM.NSSM
nssm install NetMon "C:\data\repos\NetworkStatus\.venv\Scripts\uvicorn.exe" "web.app:app --host 0.0.0.0 --port 8080 --app-dir C:\data\repos\NetworkStatus"
nssm set NetMon AppDirectory "C:\data\repos\NetworkStatus"
nssm start NetMon
```

Use NSSM when you want the monitor to run on a headless server or before anyone logs in.

---

## CLI commands

All commands work via `./netmon.sh` (macOS/Linux) or `.\netmon.ps1` (Windows).

### `monitor` — continuous monitoring

```bash
./netmon.sh monitor                                    # latency every 30s
./netmon.sh monitor --interval 60 --speed-interval 3600  # speed test hourly
./netmon.sh monitor --host 1.1.1.1 --quiet
./netmon.sh monitor --alerts-config alerts.json        # load threshold profile
```

### `ping` — one-off latency check

```bash
./netmon.sh ping
./netmon.sh ping --host 1.1.1.1 --count 20
```

### `speed` — one-off speed test (Netflix fast.com)

```bash
./netmon.sh speed
```

### `report` — tables and terminal graphs

```bash
./netmon.sh report summary
./netmon.sh report summary --hours 6
./netmon.sh report table checks --last 20
./netmon.sh report table speeds
./netmon.sh report graph latency
./netmon.sh report graph speed
./netmon.sh report graph all
```

### `query` — ask Claude about your network

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export ANTHROPIC_WORKSPACE_ID=wrkspc-...   # required for identity-linked keys

./netmon.sh query "Was my connection stable last night?"
./netmon.sh query "When was my worst latency spike?" --hours 48
./netmon.sh query --interactive
```

---

## Alerts

Create a JSON config file to define thresholds:

```json
{
  "latency_ms": 150,
  "jitter_ms": 30,
  "pkt_loss_pct": 5,
  "download_mbps": 50,
  "upload_mbps": 10,
  "cooldown_sec": 300,
  "ntfy_topic": "your-ntfy-topic"
}
```

Pass it to the monitor:

```bash
./netmon.sh monitor --alerts-config alerts.json
```

Or set thresholds live in the web UI (Settings tab) — changes take effect on the next check without restarting.

**Alert backends (all active simultaneously if configured):**

| Platform | Method |
|----------|--------|
| macOS | `osascript` system notification |
| Linux | `notify-send` desktop notification |
| Any | [ntfy.sh](https://ntfy.sh) push notification |
| Any | In-browser alert panel (web UI) |

---

## Interface detection and preference

On multihomed devices (Ethernet + WiFi + VPN), NetMon automatically selects the best interface for measurements:

**Priority: Wired > WiFi > VPN > Other**

Each measurement result includes which interface was used. The CLI shows a color-coded badge (green = wired, yellow = WiFi, magenta = VPN) and the web UI shows an icon next to the stats.

---

## What's measured

| Metric | Method | Field |
|--------|--------|-------|
| Latency | ICMP ping avg RTT (TCP fallback if ICMP blocked) | `latency_ms` |
| Jitter | Ping RTT standard deviation | `jitter_ms` |
| Packet loss | % packets lost per check | `pkt_loss` |
| Download speed | Netflix fast.com CDN | `download_mbps` |
| Upload speed | Netflix fast.com CDN | `upload_mbps` |

History is stored in `~/.netmon/history.db` (SQLite). Override with `--db /path/to/file.db`.

---

## Colour coding

| Colour | Latency | Jitter | Loss |
|--------|---------|--------|------|
| Green | < 50 ms | < 10 ms | < 1% |
| Yellow | 50–150 ms | 10–30 ms | 1–5% |
| Red | > 150 ms | > 30 ms | > 5% |

---

## Remote access

The web UI binds to `0.0.0.0:8080` by default. For secure remote access, [Tailscale](https://tailscale.com) is the simplest option — no port forwarding or HTTPS setup required.

Override host/port via environment variables:

```bash
NETMON_HOST=127.0.0.1 NETMON_PORT=9090 ./serve.sh
```
