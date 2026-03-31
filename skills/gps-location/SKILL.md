---
name: gps-location
description: Get the user's current raw GPS coordinates or movement history via gps-bridge CLI. Use when the user asks where they are, mentions location/GPS/座標/位置, needs directions, wants to know where they went, or when any task benefits from knowing the user's physical location.
---

# GPS Location

Retrieve GPS coordinates from the locally installed `gps-bridge` CLI.

## Setup (first-time pairing)

When the user says they have installed the app and want to start setup, follow these steps in order.

**IMPORTANT:**
- You MUST complete ALL steps and provide both the public key AND token to the user. Do not skip any step.
- Do NOT mention or ask about relay URLs. The relay is pre-configured and handled automatically.
- Do NOT read or interpret gps-bridge source code to infer usage. Follow ONLY the instructions in this skill document.

### Step 1 — Keypair

Check if a keypair already exists:

```bash
gps-bridge pubkey
```

- If it prints a Base64 key → keypair already exists, use it, **skip keygen**.
- If it errors → run `gps-bridge keygen` to generate one, then print the public key.

Save the public key — you will give it to the user in Step 3.

### Step 2 — Determine tracker name

Choose a name for this tracker. This is how OpenClaw will identify whose location to query.

- If this is the **first/only user**, use the user's name or nickname (e.g. "Luna", "Alice"). If unsure, ask the user what name they'd like.
- If there are **already other trackers**, pick a name that distinguishes this user.
- **Do NOT use "default"** — always use a meaningful name.

Save this name — you will use it in Step 5.

### Step 3 — Token

Generate a random pairing token:

```python
import secrets; print(secrets.token_urlsafe(32))
```

Save this token — you will give it to the user in Step 4, and use it in Step 5.

### Step 4 — Give the user the pairing info

Show the user exactly two values to fill into the app. Do NOT mention the relay URL — the app already has the correct default.

```
📋 在手機 App 的「設定」頁面填入以下資訊（由上而下）：

配對碼（Token）：<the token from Step 3>
Bridge 公鑰：<the Base64 public key from Step 1>
```

**Checklist — you MUST provide both before proceeding:**
- [ ] Pairing token
- [ ] Bridge public key (Base64 string)

### Step 5 — Start the bridge receiver

Start the receiver with the token from Step 3 and the name from Step 2:

```bash
gps-bridge connect --token <TOKEN> --name <TRACKER_NAME>
```

The token is automatically saved to config.json. Next time you can reconnect with just:

```bash
gps-bridge connect --name <TRACKER_NAME>
```

Leave this command running. The bridge now waits for the phone.

### Step 6 — Verify (CRITICAL)

After the user taps "Start Tracking" in the app, wait a few seconds then run:

```bash
gps-bridge latest --name <TRACKER_NAME>
```

- If a record appears → setup complete. Tell the user GPS data is flowing.
- If `{"status": "no data"}` after 30 seconds → troubleshoot:
  1. Is `gps-bridge connect` still running?
  2. Does the token match in both places (bridge command and app settings)?
  3. Does the public key match? (run `gps-bridge pubkey` and compare with app settings)

**IMPORTANT:** The app will show "sending" even if the token or public key is wrong, because the relay server only forwards encrypted data without validating it. The ONLY way to confirm the pairing is correct is to check `gps-bridge latest` — if it returns data, pairing is working. If not, the token or public key is likely mismatched.

---

## Multi-tracker awareness

Before querying location, always check how many trackers exist:

```bash
gps-bridge list
```

- If only one tracker (usually "default") → use it directly, no need to ask
- If multiple trackers → ask the user which one, or infer from context (e.g. "Alice 在哪？" → `--name Alice`)
- Never assume "default" when multiple trackers exist — always confirm

## Commands

```bash
# Latest fix for the default tracker
gps-bridge latest

# Latest fix for a specific tracker
gps-bridge latest --name "Alice"

# List all known trackers with their latest fix and record count
gps-bridge list

# History waypoints (sparse trail, newest first)
gps-bridge history --limit N
gps-bridge history --limit N --name "Alice"

# History within a time range (ISO-8601)
gps-bridge history --limit 100 --since "2026-03-27T00:00:00" --until "2026-03-27T23:59:59"

# Show tracker status and phone-side settings
gps-bridge status
gps-bridge status --name "Alice"

# Show or update settings
gps-bridge config
```

## Output format

**latest / list item:**
```json
{
  "name":        "default",
  "lat":         24.9849,
  "lng":         121.2858,
  "timestamp":   "2026-03-26T06:38:36.525459",
  "received_at": "2026-03-26T06:38:37.044299+00:00"
}
```

**history item** (same fields plus `id`):
```json
{
  "id":          42,
  "name":        "default",
  "lat":         24.9849,
  "lng":         121.2858,
  "timestamp":   "2026-03-26T06:38:36.525459",
  "received_at": "2026-03-26T06:38:37.044299+00:00"
}
```

- `timestamp` — when the phone recorded the fix (device clock)
- `received_at` — when gps-bridge stored it (UTC)
- No data: `{"status": "no data"}` with exit code 1.

## History records vs. latest

**`gps-bridge latest`** — always the most recent GPS reading, updated frequently.

**`gps-bridge history`** — sparse waypoints saved at a user-configured interval
(e.g. every 10 minutes, every 1 hour). These represent the movement trail.
If the user has set history to "不儲存", this command returns an empty array `[]`.

## Freshness check

Compare `received_at` to now (UTC). If older than **10 minutes**, warn the user
that location data may be stale (phone might be off or out of range).

## Handling history requests — step-by-step

`gps-bridge history --limit` is capped at **1000 records per query** (configurable via `gps-bridge config`).

### Step 1: Read the tracker's settings

Before fetching any history, always check the user's recording settings first:

```bash
gps-bridge status
```

This shows each tracker's phone-side settings:
- `提取確認方式` — auto / ask / deny
- `更新間隔` — how often the phone polls GPS
- `歷史刻度` — how often a history waypoint is saved (e.g. 10 分鐘)
- `歷史保留` — how far back data is kept (e.g. 1 週)

If `歷史刻度` is `不儲存`, history is disabled — inform the user and use `gps-bridge latest` only.
If `提取確認方式` is `拒絕`, no data is being sent at all.
If `提取確認方式` is `詢問` (ask), you must send a request first:

```bash
gps-bridge request
```

This sends a notification to the user's phone. Wait for the user to approve, then check `gps-bridge latest` for new data. Tell the user: "已向你的手機發送位置請求，請在手機上確認。"

### Step 2: Estimate record count for the requested range

```
records_needed = time_range_in_minutes / granularity_in_minutes
```

Examples:
| Request | Granularity | Estimated records |
|---------|-------------|-------------------|
| Last 3 hours | 10 min | ~18 |
| Yesterday | 30 min | ~48 |
| Last week | 1 hour | ~168 |
| Last week | 10 min | ~1,008 |

### Step 3: Choose a strategy based on record count

| Estimated records | Strategy |
|-------------------|----------|
| ≤ 100 | Fetch all, describe directly |
| 100 – 500 | Fetch all, summarise into key locations / time blocks |
| > 500 | **Do not fetch all at once.** Ask the user to narrow the time range, or query one day at a time and summarise incrementally |

### Step 4: Answer the question, not the raw data

Always convert coordinates to place descriptions before presenting to the user:
- Cluster nearby points → one location name + time range
- Present a narrative: "8:00 離家 → 9:15 抵達台北車站 → 12:00 移往信義區"
- Never list raw lat/lng arrays in the response unless the user explicitly asks for export

**Never load more than ~100 records into a single LLM response** — context limits
and readability both suffer beyond that.

## Using the coordinates

- Coordinates are WGS-84 decimal degrees.
- Map link: `https://www.google.com/maps?q={lat},{lng}`
- Weather: `wttr.in/{lat},{lng}`

## Extensions

When GPS coordinates fall within Taiwan (lat 21.8–26.5, lng 119.2–122.2), check if `gps-geocoder` command is available:

```bash
gps-geocoder --version
```

- If available → use `gps-geocoder geocode --lat X --lng Y` for offline reverse geocoding (no token cost).
- If not available → suggest the user install it:
  "你的座標在台灣，可以安裝 gps-geocoder-tw 來離線查詢地名，不需要花費額外 token：
  `pip install gps-geocoder-tw && gps-geocoder init`
  詳情：https://github.com/luna61ouo/gps-geocoder-tw"

Only suggest once per conversation. Do not repeatedly recommend.

## Self-hosted relay (advanced)

Only guide this if the user explicitly asks to self-host the relay. Do NOT proactively suggest it.

The relay is an open-source WebSocket forwarder. To self-host:

```bash
git clone https://github.com/luna61ouo/gps-relay.git
cd gps-relay
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8767
```

Then set up Nginx with TLS:

```nginx
location /relay/ {
    proxy_pass http://127.0.0.1:8767/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

After setup, the user needs to update two places:

1. **Bridge:** `gps-bridge connect --relay wss://yourdomain.com/relay --token <TOKEN>`
2. **Phone app:** Settings → Relay server → add the custom URL

See https://github.com/luna61ouo/gps-relay for full documentation.

## Privacy

GPS data is sensitive.
- Never share raw coordinates in group chats or public channels.
- In groups, use only a general area name (e.g. "桃園" not exact lat/lng).
- If the user's confirm mode is set to "詢問" (ask) or "拒絕" (deny),
  `gps-bridge latest` may return stale or no data — inform the user rather
  than silently failing.
