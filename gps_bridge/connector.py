"""
connector.py - WebSocket client that connects to gps-relay and receives GPS data.

Flow:
    gps-bridge connect --relay wss://... --token xxx
        ↓
    Connect to relay WebSocket
        ↓
    Receive encrypted GPS message from phone (via relay)
        ↓
    Decrypt with local private key
        ↓
    Store in SQLite (same DB as before)
"""

from __future__ import annotations

import json
import logging

import websockets
from cryptography.exceptions import InvalidTag

from gps_bridge.config import load_private_key
from gps_bridge.crypto import decrypt_payload
from gps_bridge.storage import init_db, insert_location

logger = logging.getLogger("gps_bridge.connector")


async def run(relay_url: str, token: str) -> None:
    """
    Connect to the relay and receive GPS messages until interrupted.

    Args:
        relay_url: Base WebSocket URL of the relay, e.g. wss://example.com/relay
        token:     Pairing token shared with the phone app.
    """
    init_db()
    private_key = load_private_key()

    ws_url = f"{relay_url.rstrip('/')}/ws/{token}"
    logger.info("Connecting to relay: %s", ws_url)
    print(f"Connecting to relay: {ws_url}")
    print("Waiting for GPS data from phone... (Ctrl+C to stop)")

    async for websocket in websockets.connect(ws_url):
        try:
            async for raw in websocket:
                _handle_message(raw, private_key)
        except websockets.ConnectionClosed:
            logger.warning("Connection closed, reconnecting...")
            print("Connection closed, reconnecting...")


def _handle_message(raw: str, private_key) -> None:
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

    insert_location(float(lat), float(lng), str(timestamp))
    print(f"[ok] {timestamp}  lat={lat:.6f}  lng={lng:.6f}")
