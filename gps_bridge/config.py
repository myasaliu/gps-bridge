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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
