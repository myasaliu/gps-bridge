"""
config.py - Configuration file management for gps-bridge.

Config is stored at ~/.gps-bridge/config.json with the following schema:
    {
        "private_key": "<base64 encoded raw X25519 private key>",
        "public_key":  "<base64 encoded raw X25519 public key>"
    }

The config directory (~/.gps-bridge/) is created on first use.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from gps_bridge.crypto import (
    private_key_from_bytes,
    private_key_to_bytes,
    public_key_from_bytes,
    public_key_to_bytes,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GPS_BRIDGE_DIR = Path.home() / ".gps-bridge"
CONFIG_FILE = GPS_BRIDGE_DIR / "config.json"
DB_FILE = GPS_BRIDGE_DIR / "locations.db"


# ---------------------------------------------------------------------------
# Settings schema
# ---------------------------------------------------------------------------

#: Default values for all user-configurable parameters.
#: Edit ~/.gps-bridge/config.json → "settings" to override.
DEFAULT_RELAY_URL = "wss://openclaw-gps-track.duckdns.org/relay"

SETTINGS_DEFAULTS: dict[str, Any] = {
    # How many non-history (latest-only) records to keep per tracker.
    # These are overwritten frequently; 2 is enough for redundancy.
    "max_latest_records": 2,

    # Hard cap on the number of records returned by `gps-bridge history --limit`.
    # Prevents accidentally loading thousands of coordinates into a single LLM response.
    # Raise this only if you need bulk data export to a non-LLM tool.
    "max_history_limit": 1000,

    # Display timezone for timestamps in CLI output.
    # null  → auto-detect from the latest GPS coordinates (recommended)
    # str   → IANA timezone name, e.g. "Asia/Taipei", "America/New_York"
    "timezone": None,
}


def ensure_dir() -> None:
    """Create the ~/.gps-bridge/ directory if it does not yet exist."""
    GPS_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Low-level JSON helpers
# ---------------------------------------------------------------------------


def _read_raw() -> dict[str, Any]:
    """Return parsed config JSON, or an empty dict if the file does not exist."""
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_raw(data: dict[str, Any]) -> None:
    """Write *data* to the config file (creates directory if needed)."""
    ensure_dir()
    with CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    # Restrict to owner read/write only (private key must not be world-readable)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_display_timezone(lat: float | None = None, lng: float | None = None) -> str:
    """
    Return the IANA timezone name to use for display.

    Priority:
      1. User setting (config.json → settings → timezone)
      2. Auto-detect from provided GPS coordinates via timezonefinder
      3. Fallback: "UTC"

    Args:
        lat: Latitude (used for auto-detect when timezone setting is null).
        lng: Longitude (used for auto-detect when timezone setting is null).
    """
    tz = load_settings().get("timezone")
    if tz:
        return tz

    if lat is not None and lng is not None:
        try:
            from timezonefinder import TimezoneFinder
            detected = TimezoneFinder().timezone_at(lat=lat, lng=lng)
            if detected:
                return detected
        except Exception:
            pass

    return "UTC"


def load_settings() -> dict[str, Any]:
    """
    Return merged settings: SETTINGS_DEFAULTS overridden by values in config.json.

    Users can edit ~/.gps-bridge/config.json and add/modify the "settings" object:
        {
            "private_key": "...",
            "public_key":  "...",
            "settings": {
                "max_latest_records": 2,
                "max_history_limit":  1000
            }
        }
    """
    stored = _read_raw().get("settings", {})
    return {key: stored.get(key, default) for key, default in SETTINGS_DEFAULTS.items()}


def save_settings(updates: dict[str, Any]) -> None:
    """
    Persist *updates* into the "settings" block of config.json.

    Unknown keys in *updates* are ignored (only SETTINGS_DEFAULTS keys are written).
    Existing config values (keypair etc.) are preserved.
    """
    data = _read_raw()
    current = data.get("settings", {})
    for key in SETTINGS_DEFAULTS:
        if key in updates:
            current[key] = updates[key]
    data["settings"] = current
    _write_raw(data)


def config_exists() -> bool:
    """Return True if a config file with a keypair already exists."""
    if not CONFIG_FILE.exists():
        return False
    data = _read_raw()
    return "private_key" in data and "public_key" in data


def save_keypair(private_key: X25519PrivateKey, public_key: X25519PublicKey) -> None:
    """
    Persist the keypair to ~/.gps-bridge/config.json.

    Any existing config values are preserved; only the key fields are updated.
    """
    data = _read_raw()
    data["private_key"] = base64.b64encode(private_key_to_bytes(private_key)).decode()
    data["public_key"] = base64.b64encode(public_key_to_bytes(public_key)).decode()
    _write_raw(data)


def load_private_key() -> X25519PrivateKey:
    """
    Load the X25519 private key from config.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError:          If the private_key field is absent.
        ValueError:        If the stored value cannot be decoded.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_FILE}. "
            "Run `gps-bridge keygen` first."
        )
    data = _read_raw()
    if "private_key" not in data:
        raise KeyError(
            "private_key not found in config. Run `gps-bridge keygen` first."
        )
    try:
        raw = base64.b64decode(data["private_key"])
    except Exception as exc:
        raise ValueError(f"Failed to base64-decode private_key: {exc}") from exc
    return private_key_from_bytes(raw)


def load_public_key() -> X25519PublicKey:
    """
    Load the X25519 public key from config.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError:          If the public_key field is absent.
        ValueError:        If the stored value cannot be decoded.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_FILE}. "
            "Run `gps-bridge keygen` first."
        )
    data = _read_raw()
    if "public_key" not in data:
        raise KeyError(
            "public_key not found in config. Run `gps-bridge keygen` first."
        )
    try:
        raw = base64.b64decode(data["public_key"])
    except Exception as exc:
        raise ValueError(f"Failed to base64-decode public_key: {exc}") from exc
    return public_key_from_bytes(raw)


def save_connection_token(token: str) -> None:
    """Persist the pairing token into config.json for reuse."""
    data = _read_raw()
    data["token"] = token
    _write_raw(data)


def load_connection_token() -> str | None:
    """Load the stored pairing token, or None if not set."""
    data = _read_raw()
    return data.get("token")


def load_public_key_b64() -> str:
    """Return the base64-encoded public key string directly from config."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_FILE}. "
            "Run `gps-bridge keygen` first."
        )
    data = _read_raw()
    if "public_key" not in data:
        raise KeyError(
            "public_key not found in config. Run `gps-bridge keygen` first."
        )
    return data["public_key"]
