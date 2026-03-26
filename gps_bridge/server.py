"""
server.py - FastAPI server for gps-bridge.

Endpoints:

    POST /gps
        Receive an encrypted GPS payload from the phone, decrypt it, and
        store it in SQLite.

        Request body (JSON):
            {
                "ephemeral_pub": "<base64>",
                "nonce":         "<base64>",
                "ciphertext":    "<base64>",
                "tag":           "<base64>"
            }

        Responses:
            200  {"status": "ok"}
            400  {"status": "error", "message": "<reason>"}

    GET /gps/latest
        Return the most-recently stored location.

        Responses:
            200  {"lat": float, "lng": float, "timestamp": str, "received_at": str}
            404  {"status": "no data"}

The server is started via `gps-bridge serve` (see main.py).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gps_bridge import __version__
from gps_bridge.config import load_private_key
from gps_bridge.crypto import decrypt_payload
from gps_bridge.storage import get_latest, init_db, insert_location

logger = logging.getLogger("gps_bridge.server")


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise resources on startup; clean up on shutdown."""
    init_db()
    logger.info("Database initialised.")
    yield
    logger.info("Server shutting down.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="gps-bridge",
    version=__version__,
    description=(
        "Receives AES-256-GCM encrypted GPS payloads from a companion phone app "
        "and exposes the latest location for OpenClaw."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "message": message},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/gps")
async def receive_gps(request: Request) -> JSONResponse:
    """
    Decrypt an incoming GPS payload and persist it.

    The phone sends an ephemeral X25519 public key alongside an AES-GCM
    encrypted JSON body.  The server performs ECDH with its static private key
    and HKDF to derive the AES key, then decrypts and stores the location.
    """
    # --- Parse request body -------------------------------------------------
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return _error("Request body is not valid JSON.")

    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.")

    # --- Load server private key --------------------------------------------
    try:
        private_key = load_private_key()
    except (FileNotFoundError, KeyError) as exc:
        logger.error("Keypair not found: %s", exc)
        return _error("Server keypair not configured. Run `gps-bridge keygen`.", 500)

    # --- Decrypt payload ----------------------------------------------------
    try:
        plaintext = decrypt_payload(body, private_key)
    except ValueError as exc:
        logger.warning("Payload decode/validation error: %s", exc)
        return _error(f"Invalid payload: {exc}")
    except InvalidTag:
        logger.warning("AES-GCM authentication tag verification failed.")
        return _error("Decryption failed: authentication tag mismatch.")
    except Exception as exc:
        logger.error("Unexpected decryption error: %s", exc)
        return _error("Decryption failed.")

    # --- Parse decrypted JSON ------------------------------------------------
    try:
        location: dict[str, Any] = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        logger.warning("Decrypted payload is not valid JSON: %s", exc)
        return _error("Decrypted payload is not valid JSON.")

    # --- Validate required fields -------------------------------------------
    lat = location.get("lat")
    lng = location.get("lng")
    timestamp = location.get("timestamp")

    if lat is None or lng is None or timestamp is None:
        return _error("Decrypted payload must contain lat, lng, and timestamp.")

    if not isinstance(lat, (int, float)):
        return _error("lat must be a number.")
    if not isinstance(lng, (int, float)):
        return _error("lng must be a number.")
    if not isinstance(timestamp, str):
        return _error("timestamp must be a string.")

    if not (-90.0 <= float(lat) <= 90.0):
        return _error("lat must be between -90 and 90.")
    if not (-180.0 <= float(lng) <= 180.0):
        return _error("lng must be between -180 and 180.")

    # --- Persist ------------------------------------------------------------
    try:
        insert_location(float(lat), float(lng), timestamp)
    except Exception as exc:
        logger.error("Database insert failed: %s", exc)
        return _error("Failed to store location.", 500)

    logger.info("Stored location: lat=%.6f, lng=%.6f, ts=%s", lat, lng, timestamp)
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.get("/gps/latest")
async def get_latest_location() -> JSONResponse:
    """Return the most recently stored GPS location."""
    try:
        record = get_latest()
    except Exception as exc:
        logger.error("Database read failed: %s", exc)
        return _error("Failed to read location.", 500)

    if record is None:
        return JSONResponse(status_code=404, content={"status": "no data"})

    return JSONResponse(
        status_code=200,
        content={
            "lat": record["lat"],
            "lng": record["lng"],
            "timestamp": record["timestamp"],
            "received_at": record["received_at"],
        },
    )


@app.get("/health")
async def health() -> JSONResponse:
    """Simple health-check endpoint."""
    return JSONResponse(status_code=200, content={"status": "ok", "version": __version__})
