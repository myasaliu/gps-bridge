"""
gps-bridge - Standalone encrypted GPS receiver bridge for OpenClaw.

Receives AES-256-GCM encrypted GPS payloads (X25519 ECDH key exchange) from a
companion phone app and exposes them via a local FastAPI server for consumption
by OpenClaw skills.
"""

__version__ = "0.1.0"
__author__ = "gps-bridge contributors"
__license__ = "MIT"
