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
        received_at TEXT    NOT NULL    -- ISO-8601 UTC, set by the server
    );

A hard cap of MAX_RECORDS (1000) most-recent rows per name is enforced after each insert.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from gps_bridge.config import DB_FILE, ensure_dir


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RECORDS = 1000

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL DEFAULT 'default',
    lat         REAL    NOT NULL,
    lng         REAL    NOT NULL,
    timestamp   TEXT    NOT NULL,
    received_at TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_locations_name_received_at
    ON locations (name, received_at DESC);
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

    Safe to call multiple times. Also migrates existing DBs that lack the
    'name' column by adding it with a default value of 'default'.
    Migration runs before index creation so the index can reference 'name'.
    """
    with _get_conn() as conn:
        conn.execute(_CREATE_TABLE_SQL)

        # Migration: add 'name' column before creating the index that depends on it
        cols = [row[1] for row in conn.execute("PRAGMA table_info(locations)")]
        if "name" not in cols:
            conn.execute("ALTER TABLE locations ADD COLUMN name TEXT NOT NULL DEFAULT 'default'")

        conn.execute(_CREATE_INDEX_SQL)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def insert_location(lat: float, lng: float, timestamp: str, name: str = "default") -> None:
    """
    Insert a new GPS record and prune old records to stay within MAX_RECORDS per name.

    Args:
        lat:       Latitude in decimal degrees.
        lng:       Longitude in decimal degrees.
        timestamp: ISO-8601 timestamp string from the phone payload.
        name:      Tracker identifier (e.g. "Alice"). Defaults to 'default'.
    """
    received_at = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO locations (name, lat, lng, timestamp, received_at) VALUES (?, ?, ?, ?, ?)",
            (name, lat, lng, timestamp, received_at),
        )
        # Prune oldest rows for this name beyond MAX_RECORDS
        conn.execute(
            """
            DELETE FROM locations
            WHERE name = ? AND id NOT IN (
                SELECT id FROM locations
                WHERE name = ?
                ORDER BY received_at DESC
                LIMIT ?
            )
            """,
            (name, name, MAX_RECORDS),
        )


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
    Return the *limit* most-recently received locations, newest first.

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
                "WHERE name = ? ORDER BY received_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, lat, lng, timestamp, received_at FROM locations "
                "ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


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
