"""
main.py - CLI entry point for gps-bridge.

Commands:
    gps-bridge keygen                           Generate a new X25519 keypair.
    gps-bridge connect --token T [--relay URL]  Receive GPS from phone via relay.
    gps-bridge latest [--name NAME]             Print the latest GPS fix as JSON.
    gps-bridge history [--limit N] [--name N]   Print recent GPS history as JSON.
    gps-bridge list                             List all trackers with latest fix.
    gps-bridge pubkey                           Print the current public key.
    gps-bridge config [--max-latest-records N]  Show or update settings.
    gps-bridge status [--name NAME]             Show tracker status and phone settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import click

from gps_bridge.config import (
    CONFIG_FILE,
    DEFAULT_RELAY_URL,
    GPS_BRIDGE_DIR,
    SETTINGS_DEFAULTS,
    config_exists,
    get_display_timezone,
    load_public_key_b64,
    load_settings,
    save_keypair,
    save_settings,
)
from gps_bridge.crypto import generate_keypair, public_key_to_b64
from gps_bridge.storage import get_history, get_latest, get_tracker_settings, get_trackers, init_db


# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------


def _add_local_time(record: dict) -> dict:
    """
    Add a 'local_time' field (received_at converted to local timezone) to a record.
    Also adds a 'timezone' field showing which timezone was used.
    Returns a new dict; does not mutate the original.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    lat = record.get("lat")
    lng = record.get("lng")
    tz_name = get_display_timezone(lat=lat, lng=lng)

    result = dict(record)
    received_at = record.get("received_at")
    if received_at:
        try:
            dt = datetime.fromisoformat(received_at)
            local_dt = dt.astimezone(ZoneInfo(tz_name))
            result["local_time"] = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except (ZoneInfoNotFoundError, ValueError):
            pass
    result["timezone"] = tz_name
    return result


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


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--relay",
    default=DEFAULT_RELAY_URL,
    hidden=True,
    help="Override the default relay URL (advanced / self-hosted only).",
)
@click.option(
    "--token",
    default=None,
    help="Pairing token. If omitted, uses the previously saved token.",
)
@click.option(
    "--name",
    default="default",
    show_default=True,
    help="Tracker identifier for this connection (e.g. Alice).",
)
def connect(relay: str, token: str | None, name: str) -> None:
    """Connect to the relay and receive encrypted GPS from the phone."""
    from gps_bridge.config import load_connection_token, save_connection_token

    if not config_exists():
        click.echo(
            "No keypair found. Run `gps-bridge keygen` before connecting.",
            err=True,
        )
        sys.exit(1)

    # Resolve token: CLI arg > saved in config > error
    if token:
        save_connection_token(token)
    else:
        token = load_connection_token()
        if not token:
            click.echo(
                "No token provided and no saved token found.\n"
                "Run: gps-bridge connect --token <YOUR_TOKEN>",
                err=True,
            )
            sys.exit(1)
        click.echo(f"Using saved token from config.")

    from gps_bridge.connector import run
    try:
        asyncio.run(run(relay, token, name=name))
    except KeyboardInterrupt:
        click.echo("\nStopped.")


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


@cli.command()
def request() -> None:
    """Send a location request to the phone (for 'ask' mode)."""
    from gps_bridge.connector import send_location_request
    success = asyncio.run(send_location_request())
    if success:
        click.echo("Location request sent. Waiting for phone to respond...")
    else:
        click.echo(
            "Failed to send request. Is gps-bridge connect running?",
            err=True,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# latest
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--name",
    default=None,
    help="Tracker name to query. Omit to get the latest across all trackers.",
)
def latest(name: str | None) -> None:
    """Print the latest GPS coordinates as JSON."""
    init_db()
    record = get_latest(name=name)
    if record is None:
        click.echo(json.dumps({"status": "no data"}))
        sys.exit(1)
    click.echo(json.dumps(_add_local_time(record), indent=2))


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
@click.option(
    "--name",
    default=None,
    help="Tracker name to query. Omit to get history across all trackers.",
)
@click.option(
    "--since",
    default=None,
    help="Only show records after this time (ISO-8601, e.g. 2026-03-28T00:00:00).",
)
@click.option(
    "--until",
    default=None,
    help="Only show records before this time (ISO-8601, e.g. 2026-03-28T23:59:59).",
)
def history(limit: int, name: str | None, since: str | None, until: str | None) -> None:
    """Show recent GPS history as a JSON array (newest first)."""
    init_db()
    records = get_history(limit=limit, name=name, since=since, until=until)
    if not records:
        click.echo(json.dumps([]))
        return
    click.echo(json.dumps([_add_local_time(r) for r in records], indent=2))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command(name="list")
def list_trackers() -> None:
    """List all known trackers with their latest fix and record count."""
    init_db()
    trackers = get_trackers()
    if not trackers:
        click.echo(json.dumps([]))
        return
    click.echo(json.dumps(trackers, indent=2))


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
# status
# ---------------------------------------------------------------------------


def _fmt_interval(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分鐘"
    return f"{seconds // 3600} 小時"


def _fmt_retention(hours: int | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 0:
        return "無上限"
    if hours < 24:
        return f"{hours} 小時"
    if hours < 168:
        return f"{hours // 24} 天"
    if hours < 720:
        return f"{hours // 168} 週"
    return f"{hours // 720} 個月"


@cli.command()
@click.option(
    "--name",
    default=None,
    help="Tracker name. Omit to show all trackers.",
)
@click.option(
    "--json", "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON.",
)
def status(name: str | None, as_json: bool) -> None:
    """Show each tracker's latest position and phone-side settings."""
    init_db()

    settings_list = get_tracker_settings(name=name)
    trackers = {t["name"]: t for t in get_trackers()}

    if as_json:
        output = []
        for s in settings_list:
            entry = dict(s)
            if s["name"] in trackers:
                t = trackers[s["name"]]
                entry["lat"] = t["lat"]
                entry["lng"] = t["lng"]
                entry["received_at"] = t["received_at"]
                tz = get_display_timezone(lat=t["lat"], lng=t["lng"])
                entry["timezone"] = tz
                entry.update(_add_local_time(t))
            output.append(entry)
        click.echo(json.dumps(output, indent=2))
        return

    if not settings_list and not trackers:
        click.echo("No tracker data found. Is gps-bridge connect running?")
        return

    # Human-readable output
    names = list({s["name"] for s in settings_list} | set(trackers.keys()))
    if name:
        names = [n for n in names if n == name]

    for tracker_name in sorted(names):
        s = next((x for x in settings_list if x["name"] == tracker_name), {})
        t = trackers.get(tracker_name)

        click.echo(f"\nTracker: {tracker_name}")
        click.echo("  ── 手機設定 ──────────────────────────────")
        click.echo(f"  提取確認方式  : {s.get('confirm_mode', 'unknown')}")
        click.echo(f"  更新間隔      : {_fmt_interval(s.get('update_interval_seconds'))}")

        gran = s.get("history_granularity_seconds")
        click.echo(f"  歷史刻度      : {'不儲存' if gran == 0 else _fmt_interval(gran)}")
        click.echo(f"  歷史保留      : {_fmt_retention(s.get('retention_hours'))}")
        click.echo(f"  設定更新時間  : {s.get('last_updated', 'unknown')}")

        click.echo("  ── 最新位置 ──────────────────────────────")
        if t:
            tz = get_display_timezone(lat=t["lat"], lng=t["lng"])
            from datetime import datetime
            from zoneinfo import ZoneInfo
            try:
                dt = datetime.fromisoformat(t["received_at"])
                local = dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                local = t["received_at"]
            click.echo(f"  lat / lng     : {t['lat']:.6f}, {t['lng']:.6f}")
            click.echo(f"   收到時間      : {local}  ({tz})")
            click.echo(f"  記錄筆數      : {t['count']}")
        else:
            click.echo("  (尚無定位資料)")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@cli.command(name="config")
@click.option(
    "--max-latest-records",
    default=None,
    type=int,
    help="Non-history records kept per tracker (default: 2).",
)
@click.option(
    "--max-history-limit",
    default=None,
    type=int,
    help="Max records returned by `history --limit` (default: 1000).",
)
@click.option(
    "--timezone",
    default=None,
    type=str,
    help='Display timezone, e.g. "Asia/Taipei". Use "auto" to reset to GPS-based auto-detect.',
)
def config_cmd(
    max_latest_records: int | None,
    max_history_limit: int | None,
    timezone: str | None,
) -> None:
    """Show or update gps-bridge settings in ~/.gps-bridge/config.json."""
    updates: dict = {}
    if max_latest_records is not None:
        updates["max_latest_records"] = max_latest_records
    if max_history_limit is not None:
        updates["max_history_limit"] = max_history_limit
    if timezone is not None:
        updates["timezone"] = None if timezone.lower() == "auto" else timezone

    if updates:
        save_settings(updates)
        click.echo("Settings updated.")

    current = load_settings()
    click.echo(f"\nConfig file : {CONFIG_FILE}")
    click.echo("\nCurrent settings:")
    for key, value in current.items():
        default = SETTINGS_DEFAULTS[key]
        marker = "" if value == default else "  ← (modified)"
        if key == "timezone":
            display = value if value else "auto (from GPS coordinates)"
            click.echo(f"  {key}: {display}{marker}")
        else:
            click.echo(f"  {key}: {value}{marker}")

    # Show the resolved timezone based on latest GPS fix
    resolved = get_display_timezone()
    if not current.get("timezone"):
        latest = get_latest()
        if latest:
            resolved = get_display_timezone(lat=latest["lat"], lng=latest["lng"])
        click.echo(f"  timezone (resolved): {resolved}")

    click.echo(
        "\nTo edit manually, open the config file and modify the \"settings\" block.\n"
        "Set timezone with: gps-bridge config --timezone \"Asia/Taipei\"\n"
        'Reset to auto:     gps-bridge config --timezone auto'
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
