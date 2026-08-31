# NetMon — Network Monitoring Toolkit

A Python CLI for continuous monitoring of **latency, jitter, packet loss, upload/download speed**, and **network consistency** — with history stored in SQLite and output as tables, terminal graphs, or natural-language answers via Claude.

## Quick start

```bash
cd NetworkStatus
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run via the shell wrapper:
./netmon.sh --help

# Or activate the venv and use directly:
source .venv/bin/activate
python cli.py --help
```

## Commands

### `monitor` — continuous background monitoring

```bash
# Check latency every 30s (default)
./netmon.sh monitor

# Check every 60s, run speed test every hour
./netmon.sh monitor --interval 60 --speed-interval 3600

# Custom host, quieter output
./netmon.sh monitor --host 1.1.1.1 --interval 15 --quiet
```

### `ping` — one-off latency check

```bash
./netmon.sh ping
./netmon.sh ping --host 1.1.1.1 --count 20
```

### `speed` — one-off speed test

```bash
./netmon.sh speed                  # full speedtest.net test (~20s)
./netmon.sh speed --http-only      # quick HTTP download estimate only
```

### `report` — tables and graphs

```bash
# Summary dashboard
./netmon.sh report summary
./netmon.sh report summary --hours 6

# Detailed tables
./netmon.sh report table checks --hours 12
./netmon.sh report table checks --last 20        # last 20 rows only
./netmon.sh report table speeds

# Terminal graphs
./netmon.sh report graph latency
./netmon.sh report graph speed
./netmon.sh report graph loss
./netmon.sh report graph all        # all three graphs
```

### `query` — ask Claude about your network

Requires `ANTHROPIC_API_KEY` to be set.

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# Single question
./netmon.sh query "Was my connection stable last night?"
./netmon.sh query "When was my worst latency spike?" --hours 48
./netmon.sh query "How does my upload speed compare to download?"

# Interactive session
./netmon.sh query --interactive
./netmon.sh query --interactive --hours 72
```

## What's measured

| Metric | How | Stored |
|--------|-----|--------|
| **Latency** | ICMP ping avg RTT (TCP fallback if blocked) | `checks.latency_ms` |
| **Jitter** | Ping RTT standard deviation | `checks.jitter_ms` |
| **Packet loss** | % packets lost per check | `checks.pkt_loss` |
| **Consistency** | % of checks that succeeded | derived |
| **Download speed** | speedtest-cli (speedtest.net) | `speed_tests.download_mbps` |
| **Upload speed** | speedtest-cli | `speed_tests.upload_mbps` |

## Database

History is stored in `~/.netmon/history.db` (SQLite). Override with `--db /path/to/file.db` on any command.

## Colour coding

- **Green** — good (latency < 50ms, jitter < 10ms, loss < 1%)
- **Yellow** — moderate (latency 50–150ms, jitter 10–30ms, loss 1–5%)
- **Red** — poor (above those thresholds)

## Tips

- For automated monitoring, run `./netmon.sh monitor --quiet` in a `screen` or `tmux` session or as a launchd service.
- Speed tests are slow (~20s) and consume ~100MB of data. `--speed-interval 3600` (hourly) is a reasonable default.
- The `--http-only` speed flag uses Cloudflare's speed endpoint and takes ~5s but only measures download.
