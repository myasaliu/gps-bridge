# gps-bridge

A standalone, open-source encrypted GPS bridge for [OpenClaw](https://github.com/openclaw).

`gps-bridge` receives AES-256-GCM encrypted GPS coordinates from a companion phone app via a WebSocket relay, stores them locally, and exposes them to OpenClaw skills. All traffic is end-to-end encrypted — the relay server never sees plaintext data.

---

## How it works

```
Phone App
  │  (X25519 + AES-256-GCM encrypted GPS)
  ▼
gps-relay  ←── WebSocket relay (zero-storage, forwards only)
  │
  ▼
gps-bridge connect  ←── running on your computer
  │  (decrypts and stores in SQLite)
  ▼
gps-bridge latest  ←── called by OpenClaw skill
```

- The relay server only forwards encrypted messages — it never stores or reads any data.
- Only your computer (running `gps-bridge connect`) can decrypt the GPS data.
- Open-source code on both sides means you can verify this yourself.

---

## Requirements

- Python 3.10+
- Linux / macOS / Windows

---

## Installation

```bash
pip install gps-bridge
```

Or from source:

```bash
git clone https://github.com/luna61ouo/gps-bridge.git
cd gps-bridge
pip install -e .
```

---

## Quick start

### 1. Generate a keypair

```bash
gps-bridge keygen
```

Output:
```
Keypair saved to: /home/user/.gps-bridge
Public key (share this with your phone app):
2X1/S/uES8ar3U+9nHLevhGL255NyPykacaQgweWfiQ=
```

Copy the public key — you will enter it into the phone app.

### 2. Connect to the relay

```bash
gps-bridge connect --token your-token
```

The default relay server is pre-configured. Once connected, GPS data sent from the phone will appear:
```
Waiting for GPS data from phone... (Ctrl+C to stop)
[ok] 2026-03-26T06:31:02  lat=24.984968  lng=121.285887
```

### 3. Check the latest location

```bash
gps-bridge latest
```

```json
{
  "lat": 24.9849,
  "lng": 121.2858,
  "timestamp": "2026-03-26T06:31:02.357405",
  "received_at": "2026-03-26T06:31:02.929855+00:00"
}
```

---

## Auto-start on boot (optional)

`gps-bridge connect` is a long-running process. You can run it manually each time, or set it up to start automatically on login — the choice is yours.

### Option A: Run manually

Simply run in a terminal whenever you want to receive GPS:

```bash
# First time: provide the token (it gets saved to config.json)
gps-bridge connect --token your-token

# After that: just run without --token
gps-bridge connect
```

### Option B: Auto-start with systemd (Linux)

This runs `gps-bridge connect` automatically when you log in, without needing a terminal open.

**Step 1 — Save your token (one-time):**

```bash
# Run once with --token to save it to ~/.gps-bridge/config.json
gps-bridge connect --token your-token
# Press Ctrl+C after it starts successfully
```

**Step 2 — Install the user service (no sudo required):**

```bash
mkdir -p ~/.config/systemd/user
cp gps-bridge-connect.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gps-bridge-connect
systemctl --user start gps-bridge-connect
```

**Step 3 — Check status:**

```bash
systemctl --user status gps-bridge-connect
```

**To stop auto-start:**

```bash
systemctl --user disable gps-bridge-connect
systemctl --user stop gps-bridge-connect
```

---

## CLI reference

| Command | Description |
|---|---|
| `gps-bridge keygen [--force]` | Generate a new X25519 keypair |
| `gps-bridge pubkey` | Print the current public key |
| `gps-bridge connect --token TOKEN` | Connect to relay and receive GPS |
| `gps-bridge latest [--name NAME]` | Print latest GPS fix as JSON |
| `gps-bridge history [--limit N] [--name NAME] [--since T] [--until T]` | Print history as JSON array (supports time range) |
| `gps-bridge list` | List all trackers with latest fix and record count |
| `gps-bridge status [--name NAME]` | Show tracker status and phone-side settings |
| `gps-bridge config [--timezone TZ]` | Show or update settings |

---

## OpenClaw skill

Once `gps-bridge` is installed, add the GPS skill to OpenClaw so it can query your location automatically.

**From source (git clone):**

```bash
cp -r skills/gps-location ~/.openclaw/workspace/skills/
```

**From pip install:**

```bash
BRIDGE_DIR=$(python3 -c "import gps_bridge; import os; print(os.path.dirname(gps_bridge.__file__))")
cp -r "${BRIDGE_DIR}/../skills/gps-location" ~/.openclaw/workspace/skills/
```

> The default workspace skills path is `~/.openclaw/workspace/skills/`. If your workspace is elsewhere, replace the path accordingly.

After copying, restart OpenClaw. The skill handles setup, pairing, freshness checks, and privacy (no raw coordinates in group chats).

See [`skills/gps-location/SKILL.md`](skills/gps-location/SKILL.md) for the skill definition.

---

## Extensions

`gps-bridge` provides raw GPS coordinates. The following optional packages extend its functionality:

| Package | Description |
|---------|-------------|
| [gps-geocoder-tw](https://github.com/luna61ouo/gps-geocoder-tw) | Offline reverse geocoder for Taiwan — converts coordinates to street-level location names (行政區、街道、地標) using OpenStreetMap data. No API keys, no token cost. |

Install any extension with `pip install <package>`.

---

## Security model

```
Phone app                              gps-bridge (your computer)
─────────                              ──────────────────────────
Generate ephemeral X25519 keypair
ECDH(ephemeral_priv, server_pub)  ──►  ECDH(server_priv, ephemeral_pub)
HKDF-SHA256 → AES-256 key              HKDF-SHA256 → AES-256 key (same)
AES-GCM encrypt GPS payload       ──►  AES-GCM decrypt → store in SQLite
```

- The relay server sees only encrypted binary blobs — it cannot read your location.
- A fresh ephemeral keypair is used for every GPS message (forward secrecy).
- The AES-GCM authentication tag rejects any tampered messages before storage.
- Your private key never leaves your computer (`~/.gps-bridge/config.json`).

---

## Data storage

- SQLite database at `~/.gps-bridge/locations.db`
- History retention is controlled by the phone app settings (default: 1 week)
- Single query limit: 1000 records (configurable via `gps-bridge config`)
- All data stays on your own machine

---

## Development

```bash
pip install -e ".[dev]"
pytest -v
```

---

## License

MIT
