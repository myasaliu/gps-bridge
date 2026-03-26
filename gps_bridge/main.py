"""
main.py - CLI entry point for gps-bridge.

Commands:
    gps-bridge keygen               Generate a new X25519 keypair.
    gps-bridge serve                Start the FastAPI server.
    gps-bridge latest               Print the latest GPS fix as JSON.
    gps-bridge history [--limit N]  Print recent GPS history as JSON.
    gps-bridge pubkey               Print the current public key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import click
import uvicorn

from gps_bridge.config import (
    CONFIG_FILE,
    GPS_BRIDGE_DIR,
    config_exists,
    load_public_key_b64,
    save_keypair,
)
from gps_bridge.crypto import generate_keypair, public_key_to_b64
from gps_bridge.storage import get_history, get_latest, init_db


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
    )


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="gps-bridge")
def cli() -> None:
    """
    gps-bridge - Encrypted GPS receiver bridge for OpenClaw.

    Receives AES-256-GCM encrypted GPS coordinates from a companion phone app
    and stores them locally for consumption by OpenClaw skills.
    """


# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing keypair without prompting.",
)
def keygen(force: bool) -> None:
    """Generate a new X25519 keypair and save it to ~/.gps-bridge/config.json."""
    if config_exists() and not force:
        click.echo(
            f"A keypair already exists at {CONFIG_FILE}.\n"
            "Use --force to overwrite it (this will break any paired phone apps).",
            err=True,
        )
        sys.exit(1)

    private_key, public_key = generate_keypair()
    save_keypair(private_key, public_key)

    pub_b64 = public_key_to_b64(public_key)
    click.echo(f"Keypair saved to: {GPS_BRIDGE_DIR}")
    click.echo("")
    click.echo("Public key (share this with your phone app):")
    click.echo(pub_b64)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host address to bind the server to.",
)
@click.option(
    "--port",
    default=8766,
    show_default=True,
    type=int,
    help="TCP port to listen on.",
)
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(
        ["critical", "error", "warning", "info", "debug"], case_sensitive=False
    ),
    help="Uvicorn log level.",
)
def serve(host: str, port: int, log_level: str) -> None:
    """Start the gps-bridge FastAPI server."""
    if not config_exists():
        click.echo(
            "No keypair found. Run `gps-bridge keygen` before starting the server.",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Starting gps-bridge server on {host}:{port} ...")
    click.echo(f"POST /gps          - receive encrypted GPS payload")
    click.echo(f"GET  /gps/latest   - fetch latest location")
    click.echo(f"GET  /health       - health check")

    uvicorn.run(
        "gps_bridge.server:app",
        host=host,
        port=port,
        log_level=log_level.lower(),
        reload=False,
    )


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--relay",
    required=True,
    help="Base WebSocket URL of the relay, e.g. wss://example.com/relay",
)
@click.option(
    "--token",
    required=True,
    help="Pairing token shared with the phone app.",
)
def connect(relay: str, token: str) -> None:
    """Connect to the relay and receive encrypted GPS from the phone."""
    if not config_exists():
        click.echo(
            "No keypair found. Run `gps-bridge keygen` before connecting.",
            err=True,
        )
        sys.exit(1)

    from gps_bridge.connector import run
    try:
        asyncio.run(run(relay, token))
    except KeyboardInterrupt:
        click.echo("\nStopped.")


# ---------------------------------------------------------------------------
# latest
# ---------------------------------------------------------------------------


@cli.command()
def latest() -> None:
    """Print the latest GPS coordinates as JSON."""
    init_db()
    record = get_latest()
    if record is None:
        click.echo(json.dumps({"status": "no data"}))
        sys.exit(1)
    click.echo(json.dumps(record, indent=2))


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--limit",
    default=10,
    show_default=True,
    type=click.IntRange(1, 1000),
    help="Number of recent records to display.",
)
def history(limit: int) -> None:
    """Show recent GPS history as a JSON array (newest first)."""
    init_db()
    records = get_history(limit=limit)
    if not records:
        click.echo(json.dumps([]))
        return
    click.echo(json.dumps(records, indent=2))


# ---------------------------------------------------------------------------
# pubkey
# ---------------------------------------------------------------------------


@cli.command()
def pubkey() -> None:
    """Print the current X25519 public key (base64)."""
    try:
        pub_b64 = load_public_key_b64()
    except (FileNotFoundError, KeyError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(pub_b64)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
