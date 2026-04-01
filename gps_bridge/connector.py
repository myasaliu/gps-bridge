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

from gps_bridge.config import DEFAULT_RELAY_URL, load_connection_token, load_private_key
from gps_bridge.crypto import decrypt_payload
from gps_bridge.storage import init_db, insert_location, update_phone_status, update_tracker_settings

logger = logging.getLogger("gps_bridge.connector")


async def send_location_request(name: str = "default") -> bool:
    """
    Send a location_request to the phone via relay.
    Used in 'ask' mode to request user permission before getting GPS.
    Returns True if sent successfully.
    """
    token = load_connection_token(name)
    if not token:
        logger.error("No saved token for '%s'. Run `gps-bridge connect --token <TOKEN> --name %s` first.", name, name)
        return False

    ws_url = f"{DEFAULT_RELAY_URL.rstrip('/')}/ws/{token}"
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "location_request"}))
            logger.info("Sent location_request to phone")
            return True
    except Exception as e:
        logger.error("Failed to send location_request: %s", e)
        return False


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
                response = _handle_message(raw, private_key, name)
                # Send response back to phone if handler returned one
                if response:
                    try:
                        await websocket.send(json.dumps({"type": response}))
                    except Exception:
                        pass
        except websockets.ConnectionClosed:
            logger.warning("Connection closed, reconnecting...")
            print("Connection closed, reconnecting...")


def _handle_message(raw: str, private_key, name: str) -> str | None:
    """Process a single incoming message. Returns response type to send back, or None."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received non-JSON message, ignoring.")
        return False

    # Handle plaintext control messages from the phone app
    msg_type = payload.get("type")
    if msg_type == "settings_update":
        update_tracker_settings(
            name,
            confirm_mode=payload.get("confirm_mode"),
            update_interval_seconds=payload.get("update_interval_seconds"),
            history_granularity_seconds=payload.get("history_granularity_seconds"),
            retention_hours=payload.get("retention_hours"),
        )
        logger.info("[%s] Settings updated via push: confirm_mode=%s", name, payload.get("confirm_mode"))
        print(f"[{name}] Settings updated: confirm_mode={payload.get('confirm_mode')}")
        return None
    if msg_type == "ping":
        logger.info("[%s] Received ping, sending pong", name)
        return "pong"

    try:
        plaintext = decrypt_payload(payload, private_key)
    except (ValueError, InvalidTag) as exc:
        logger.warning("Decryption failed: %s", exc)
        print(f"[warn] Decryption failed: {exc}")
        return None

    try:
        data = json.loads(plaintext)
    except json.JSONDecodeError:
        logger.warning("Decrypted payload is not valid JSON.")
        return None

    # Pubkey verification test — encrypted payload with type=pubkey_test
    if data.get("type") == "pubkey_test":
        logger.info("[%s] Pubkey test passed — decryption successful", name)
        print(f"[{name}] Pubkey test: OK")
        return "pubkey_ok"

    lat = data.get("lat")
    lng = data.get("lng")
    timestamp = data.get("timestamp")

    if lat is None or lng is None or timestamp is None:
        logger.warning("Missing lat/lng/timestamp in payload.")
        return None

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
    return "location_stored"
