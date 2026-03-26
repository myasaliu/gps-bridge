"""
storage.py - SQLite persistence layer for gps-bridge.

Database: ~/.gps-bridge/locations.db

Schema:
    CREATE TABLE locations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        lat         REAL    NOT NULL,
        lng         REAL    NOT NULL,
        timestamp   TEXT    NOT NULL,   -- ISO-8601 from the phone payload
        received_at TEXT    NOT NULL    -- ISO-8601 UTC, set by the server
    );

A hard cap of MAX_RECORDS (1000) most-recent rows is enforced after each insert.
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
    lat         REAL    NOT NULL,
    lng         REAL    NOT NULL,
    timestamp   TEXT    NOT NULL,
    received_at TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_locations_received_at
    ON locations (received_at DESC);
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

    Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    """
    with _get_conn() as conn:
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def insert_location(lat: float, lng: float, timestamp: str) -> None:
    """
    Insert a new GPS record and prune old records to stay within MAX_RECORDS.

    Args:
        lat:       Latitude in decimal degrees.
        lng:       Longitude in decimal degrees.
        timestamp: ISO-8601 timestamp string from the phone payload.
    """
    received_at = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO locations (lat, lng, timestamp, received_at) VALUES (?, ?, ?, ?)",
            (lat, lng, timestamp, received_at),
        )
        # Prune oldest rows beyond MAX_RECORDS
        conn.execute(
            """
            DELETE FROM locations
            WHERE id NOT IN (
                SELECT id FROM locations
                ORDER BY received_at DESC
                LIMIT ?
            )
            """,
            (MAX_RECORDS,),
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_latest() -> dict | None:
    """
    Return the most-recently received location as a dict, or None if the table
    is empty.

    Return shape:
        {
            "lat":         float,
            "lng":         float,
            "timestamp":   str,
            "received_at": str
        }
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT lat, lng, timestamp, received_at FROM locations "
            "ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_history(limit: int = 10) -> list[dict]:
    """
    Return the *limit* most-recently received locations, newest first.

    Args:
        limit: Maximum number of records to return (capped at MAX_RECORDS).

    Returns:
        List of dicts with keys: id, lat, lng, timestamp, received_at.
    """
    limit = max(1, min(limit, MAX_RECORDS))
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, lat, lng, timestamp, received_at FROM locations "
            "ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
