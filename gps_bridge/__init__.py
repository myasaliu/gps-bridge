"""
gps-bridge - Standalone encrypted GPS receiver bridge for OpenClaw.

Receives AES-256-GCM encrypted GPS payloads (X25519 ECDH key exchange) from a
companion phone app via a WebSocket relay and stores them locally for consumption
by OpenClaw skills.
"""

__version__ = "0.1.0"
__author__ = "gps-bridge contributors"
__license__ = "MIT"
