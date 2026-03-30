"""
storage.py - SQLite persistence layer for gps-bridge.

Database: ~/.gps-bridge/locations.db

Schema:
    CREATE TABLE locations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL DEFAULT 'default',
        lat         REAL    NOT NULL,
        lng         REAL    NOT NULL,
        timestamp   TEXT    NOT NULL,   -- ISO-8601 from the phone payload
        received_at TEXT    NOT NULL,   -- ISO-8601 UTC, set by the server
        is_history  INTEGER NOT NULL DEFAULT 0  -- 1 = history record, 0 = latest-only
    );

Records with is_history=0 are kept only for the most recent MAX_LATEST per name.
Records with is_history=1 are pruned by time (retention_hours supplied per insert).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator

from gps_bridge.config import DB_FILE, ensure_dir, load_settings

# ---------------------------------------------------------------------------
# Constants (loaded from ~/.gps-bridge/config.json → "settings")
# ---------------------------------------------------------------------------

_settings = load_settings()
MAX_RECORDS: int = _settings["max_history_limit"]
MAX_LATEST: int = _settings["max_latest_records"]

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL DEFAULT 'default',
    lat         REAL    NOT NULL,
    lng         REAL    NOT NULL,
    timestamp   TEXT    NOT NULL,
    received_at TEXT    NOT NULL,
    is_history  INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_TRACKER_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS tracker_settings (
    name                        TEXT    PRIMARY KEY,
    confirm_mode                TEXT,
    update_interval_seconds     INTEGER,
    history_granularity_seconds INTEGER,
    retention_hours             INTEGER,
    last_updated                TEXT    NOT NULL,
    phone_online                INTEGER NOT NULL DEFAULT 0,
    phone_last_seen             TEXT
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_locations_name_received_at
    ON locations (name, received_at DESC);
"""

_CREATE_HISTORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_locations_name_history
    ON locations (name, is_history, received_at DESC);
"""


# ---------------------------------------------------------------------------
# Connection / schema management
# ---------------------------------------------------------------------------


def _db_path() -> str:
    ensure_dir()
    return str(DB_FILE)


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields an open SQLite connection and commits/rolls back."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialise the database schema.

    Safe to call multiple times. Runs all migrations before creating indexes.
    """
    with _get_conn() as conn:
        conn.execute(_CREATE_TABLE_SQL)

        # Migration: add 'name' column before creating the index that depends on it
        cols = [row[1] for row in conn.execute("PRAGMA table_info(locations)")]
        if "name" not in cols:
            conn.execute("ALTER TABLE locations ADD COLUMN name TEXT NOT NULL DEFAULT 'default'")

        # Migration: add 'is_history' column
        if "is_history" not in cols:
            conn.execute("ALTER TABLE locations ADD COLUMN is_history INTEGER NOT NULL DEFAULT 0")

        conn.execute(_CREATE_TRACKER_SETTINGS_SQL)

        # Migrations: add phone status columns if upgrading from older schema
        ts_cols = [row[1] for row in conn.execute("PRAGMA table_info(tracker_settings)")]
        if "phone_online" not in ts_cols:
            conn.execute("ALTER TABLE tracker_settings ADD COLUMN phone_online INTEGER NOT NULL DEFAULT 0")
        if "phone_last_seen" not in ts_cols:
            conn.execute("ALTER TABLE tracker_settings ADD COLUMN phone_last_seen TEXT")

        conn.execute(_CREATE_INDEX_SQL)
        conn.execute(_CREATE_HISTORY_INDEX_SQL)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def insert_location(
    lat: float,
    lng: float,
    timestamp: str,
    name: str = "default",
    save_history: bool = False,
    retention_hours: int = 168,
) -> None:
    """
    Insert a new GPS record and prune old records.

    Args:
        lat:             Latitude in decimal degrees.
        lng:             Longitude in decimal degrees.
        timestamp:       ISO-8601 timestamp string from the phone payload.
        name:            Tracker identifier (e.g. "Alice"). Defaults to 'default'.
        save_history:    If True, record is tagged as a history point and pruned
                         by time. If False, only the most recent MAX_LATEST
                         non-history records per name are kept.
        retention_hours: How many hours of history to retain. -1 means unlimited.
                         Only used when save_history=True.
    """
    received_at = datetime.now(timezone.utc).isoformat()
    is_history = 1 if save_history else 0

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO locations (name, lat, lng, timestamp, received_at, is_history) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, lat, lng, timestamp, received_at, is_history),
        )

        if save_history:
            # Prune history records older than retention_hours for this name
            if retention_hours > 0:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=retention_hours)
                ).isoformat()
                conn.execute(
                    "DELETE FROM locations WHERE name = ? AND is_history = 1 AND received_at < ?",
                    (name, cutoff),
                )
        else:
            # Keep only the most recent MAX_LATEST non-history records per name
            conn.execute(
                """
                DELETE FROM locations
                WHERE name = ? AND is_history = 0 AND id NOT IN (
                    SELECT id FROM locations
                    WHERE name = ? AND is_history = 0
                    ORDER BY received_at DESC
                    LIMIT ?
                )
                """,
                (name, name, MAX_LATEST),
            )


def update_tracker_settings(
    name: str,
    *,
    confirm_mode: str | None = None,
    update_interval_seconds: int | None = None,
    history_granularity_seconds: int | None = None,
    retention_hours: int | None = None,
) -> None:
    """
    Upsert phone-side settings for a tracker.
    Called each time a GPS payload is received that contains settings fields.
    """
    last_updated = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tracker_settings
                (name, confirm_mode, update_interval_seconds,
                 history_granularity_seconds, retention_hours, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                confirm_mode                = COALESCE(excluded.confirm_mode, confirm_mode),
                update_interval_seconds     = COALESCE(excluded.update_interval_seconds, update_interval_seconds),
                history_granularity_seconds = COALESCE(excluded.history_granularity_seconds, history_granularity_seconds),
                retention_hours             = COALESCE(excluded.retention_hours, retention_hours),
                last_updated                = excluded.last_updated
            """,
            (name, confirm_mode, update_interval_seconds,
             history_granularity_seconds, retention_hours, last_updated),
        )


def get_tracker_settings(name: str | None = None) -> list[dict]:
    """
    Return phone-side settings for one or all trackers.

    Args:
        name: Tracker name. If None, returns settings for all trackers.
    """
    with _get_conn() as conn:
        if name is not None:
            rows = conn.execute(
                "SELECT * FROM tracker_settings WHERE name = ?", (name,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tracker_settings ORDER BY last_updated DESC"
            ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_latest(name: str | None = None) -> dict | None:
    """
    Return the most-recently received location as a dict, or None if no data.

    Args:
        name: Filter by tracker name. If None, returns the latest across all trackers.

    Return shape:
        {
            "name":        str,
            "lat":         float,
            "lng":         float,
            "timestamp":   str,
            "received_at": str
        }
    """
    with _get_conn() as conn:
        if name is not None:
            row = conn.execute(
                "SELECT name, lat, lng, timestamp, received_at FROM locations "
                "WHERE name = ? ORDER BY received_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT name, lat, lng, timestamp, received_at FROM locations "
                "ORDER BY received_at DESC LIMIT 1"
            ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_history(limit: int = 10, name: str | None = None) -> list[dict]:
    """
    Return the *limit* most-recently stored history records (is_history=1), newest first.

    Args:
        limit: Maximum number of records to return (capped at MAX_RECORDS).
        name:  Filter by tracker name. If None, returns history across all trackers.

    Returns:
        List of dicts with keys: id, name, lat, lng, timestamp, received_at.
    """
    limit = max(1, min(limit, MAX_RECORDS))
    with _get_conn() as conn:
        if name is not None:
            rows = conn.execute(
                "SELECT id, name, lat, lng, timestamp, received_at FROM locations "
                "WHERE name = ? AND is_history = 1 ORDER BY received_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, lat, lng, timestamp, received_at FROM locations "
                "WHERE is_history = 1 ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def update_phone_status(name: str, online: bool) -> None:
    """
    Mark the phone for the given tracker as online or offline.

    Ensures a tracker_settings row exists (upsert) so status can be tracked
    even before any GPS data has been received.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tracker_settings
                (name, last_updated, phone_online, phone_last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                phone_online    = excluded.phone_online,
                phone_last_seen = CASE
                    WHEN excluded.phone_online = 1 THEN excluded.phone_last_seen
                    ELSE phone_last_seen
                END,
                last_updated    = excluded.last_updated
            """,
            (name, now, 1 if online else 0, now if online else None),
        )


def get_phone_status(name: str | None = None) -> list[dict]:
    """
    Return phone online status for one or all trackers.

    Each dict has keys: name, phone_online (bool), phone_last_seen (ISO str or None).
    """
    with _get_conn() as conn:
        if name is not None:
            rows = conn.execute(
                "SELECT name, phone_online, phone_last_seen FROM tracker_settings WHERE name = ?",
                (name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, phone_online, phone_last_seen FROM tracker_settings ORDER BY last_updated DESC"
            ).fetchall()
    return [
        {
            "name": row["name"],
            "phone_online": bool(row["phone_online"]),
            "phone_last_seen": row["phone_last_seen"],
        }
        for row in rows
    ]


def get_trackers() -> list[dict]:
    """
    Return all known tracker names along with their latest fix and record count.

    Returns:
        List of dicts with keys: name, lat, lng, timestamp, received_at, count.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                l.name,
                l.lat,
                l.lng,
                l.timestamp,
                l.received_at,
                c.count
            FROM locations l
            JOIN (
                SELECT name, COUNT(*) AS count, MAX(received_at) AS max_received
                FROM locations
                GROUP BY name
            ) c ON l.name = c.name AND l.received_at = c.max_received
            ORDER BY l.received_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]
