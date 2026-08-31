"""SQLite persistence for network measurements."""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DB = Path.home() / ".netmon" / "history.db"


def get_conn(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS checks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            host        TEXT NOT NULL,
            latency_ms  REAL,
            jitter_ms   REAL,
            pkt_loss    REAL,
            success     INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS speed_tests (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            server       TEXT,
            download_mbps REAL,
            upload_mbps   REAL,
            ping_ms       REAL
        );

        CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(ts);
        CREATE INDEX IF NOT EXISTS idx_speed_ts  ON speed_tests(ts);
    """)
    conn.commit()


def insert_check(conn, ts: datetime, host: str, latency_ms: Optional[float],
                 jitter_ms: Optional[float], pkt_loss: float, success: bool):
    conn.execute(
        """INSERT INTO checks (ts, host, latency_ms, jitter_ms, pkt_loss, success)
           VALUES (?,?,?,?,?,?)""",
        (ts.isoformat(), host, latency_ms, jitter_ms, pkt_loss, int(success))
    )
    conn.commit()


def insert_speed(conn, ts: datetime, server: str,
                 download_mbps: float, upload_mbps: float, ping_ms: float):
    conn.execute(
        """INSERT INTO speed_tests (ts, server, download_mbps, upload_mbps, ping_ms)
           VALUES (?,?,?,?,?)""",
        (ts.isoformat(), server, download_mbps, upload_mbps, ping_ms)
    )
    conn.commit()


def fetch_checks(conn, hours: int = 24, host: Optional[str] = None) -> list[dict]:
    where = "WHERE ts >= datetime('now', ?)"
    params: list = [f"-{hours} hours"]
    if host:
        where += " AND host = ?"
        params.append(host)
    rows = conn.execute(
        f"SELECT * FROM checks {where} ORDER BY ts", params
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_speeds(conn, hours: int = 24) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM speed_tests
           WHERE ts >= datetime('now', ?)
           ORDER BY ts""",
        [f"-{hours} hours"]
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_summary(conn, hours: int = 24) -> dict:
    checks = conn.execute(
        """SELECT
             COUNT(*)                                    AS total,
             SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
             AVG(latency_ms)                             AS avg_latency,
             MIN(latency_ms)                             AS min_latency,
             MAX(latency_ms)                             AS max_latency,
             AVG(jitter_ms)                              AS avg_jitter,
             AVG(pkt_loss)                               AS avg_loss
           FROM checks
           WHERE ts >= datetime('now', ?)""",
        [f"-{hours} hours"]
    ).fetchone()

    speeds = conn.execute(
        """SELECT
             COUNT(*)             AS total,
             AVG(download_mbps)   AS avg_dl,
             MAX(download_mbps)   AS max_dl,
             MIN(download_mbps)   AS min_dl,
             AVG(upload_mbps)     AS avg_ul,
             MAX(upload_mbps)     AS max_ul,
             MIN(upload_mbps)     AS min_ul,
             AVG(ping_ms)         AS avg_ping
           FROM speed_tests
           WHERE ts >= datetime('now', ?)""",
        [f"-{hours} hours"]
    ).fetchone()

    return {"checks": dict(checks), "speeds": dict(speeds)}
