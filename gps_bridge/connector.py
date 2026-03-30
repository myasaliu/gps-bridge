"""
connector.py - WebSocket client that connects to gps-relay and receives GPS data.

Flow:
    gps-bridge connect --relay wss://... --token xxx --name "Alice"
        ↓
    Connect to relay WebSocket
        ↓
    Receive encrypted GPS message from phone (via relay)
        ↓
    Decrypt with local private key
        ↓
    Store in SQLite under the given name
"""

from __future__ import annotations

import json
import logging

import websockets
from cryptography.exceptions import InvalidTag

from gps_bridge.config import load_private_key
from gps_bridge.crypto import decrypt_payload
from gps_bridge.storage import init_db, insert_location, update_phone_status, update_tracker_settings

logger = logging.getLogger("gps_bridge.connector")


async def run(relay_url: str, token: str, name: str = "default") -> None:
    """
    Connect to the relay and receive GPS messages until interrupted.

    Args:
        relay_url: Base WebSocket URL of the relay, e.g. wss://example.com/relay
        token:     Pairing token shared with the phone app.
        name:      Tracker identifier stored with each record (e.g. "Alice").
    """
    init_db()
    private_key = load_private_key()

    ws_url = f"{relay_url.rstrip('/')}/ws/{token}"
    logger.info("Connecting to relay: %s (name=%s)", ws_url, name)
    print(f"Connecting to relay: {ws_url}")
    print(f"Tracker name: {name}")
    print("Waiting for GPS data from phone... (Ctrl+C to stop)")

    async for websocket in websockets.connect(ws_url):
        try:
            async for raw in websocket:
                # Control message from relay (not an encrypted GPS payload)
                if raw == '{"type":"peer_disconnected"}':
                    logger.info("[%s] Phone disconnected (peer_disconnected from relay)", name)
                    print(f"[{name}] Phone disconnected.")
                    update_phone_status(name, False)
                    continue
                update_phone_status(name, True)
                _handle_message(raw, private_key, name)
        except websockets.ConnectionClosed:
            logger.warning("Connection closed, reconnecting...")
            print("Connection closed, reconnecting...")


def _handle_message(raw: str, private_key, name: str) -> None:
    """Decrypt and store a single incoming message."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received non-JSON message, ignoring.")
        return

    try:
        plaintext = decrypt_payload(payload, private_key)
    except (ValueError, InvalidTag) as exc:
        logger.warning("Decryption failed: %s", exc)
        print(f"[warn] Decryption failed: {exc}")
        return

    try:
        data = json.loads(plaintext)
    except json.JSONDecodeError:
        logger.warning("Decrypted payload is not valid JSON.")
        return

    lat = data.get("lat")
    lng = data.get("lng")
    timestamp = data.get("timestamp")

    if lat is None or lng is None or timestamp is None:
        logger.warning("Missing lat/lng/timestamp in payload.")
        return

    save_history = bool(data.get("save_history", False))
    retention_hours = int(data.get("retention_hours", 168))
    insert_location(
        float(lat), float(lng), str(timestamp),
        name=name,
        save_history=save_history,
        retention_hours=retention_hours,
    )

    update_tracker_settings(
        name,
        confirm_mode=data.get("confirm_mode"),
        update_interval_seconds=data.get("update_interval_seconds"),
        history_granularity_seconds=data.get("history_granularity_seconds"),
        retention_hours=retention_hours,
    )

    history_marker = " [H]" if save_history else ""
    print(f"[{name}] {timestamp}  lat={lat:.6f}  lng={lng:.6f}{history_marker}")
