# GPS Bridge — Setup & Configuration

This file contains setup, pairing, and advanced configuration instructions.
Read this file when the user wants to: set up GPS tracking, add a new tracker, re-pair, configure self-hosted relay, or troubleshoot connection issues.

---

## First-time pairing

When the user says they have installed the app and want to start setup, follow these steps in order.

**IMPORTANT:**
- After installing gps-bridge (`pip install gps-bridge`), the **Gateway must be restarted** so the SKILL files are loaded. Tell the user to restart Gateway before proceeding.
- You MUST complete ALL steps and provide both the public key AND token to the user. Do not skip any step.
- Do NOT mention or ask about relay URLs. The relay is pre-configured and handled automatically.
- Do NOT read or interpret gps-bridge source code to infer usage. Follow ONLY the instructions in this document.

### Step 1 — Keypair

Check if a keypair already exists:

```bash
gps-bridge pubkey
```

- If it prints a Base64 key → keypair already exists, use it, **skip keygen**.
- If it errors → run `gps-bridge keygen` to generate one, then print the public key.

Save the public key — you will give it to the user in Step 4.

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

## Adding a new tracker (additional user)

Same as first-time pairing, but:
- Skip Step 1 (keypair already exists, all trackers share the same keypair)
- Step 2: choose a **different** name from existing trackers
- You will need to run a **separate** `gps-bridge connect --name <NAME>` process for each tracker

---

## Auto-start with systemd (Linux)

```bash
# Token must be saved first (run connect --token once manually)
gps-bridge connect --token <TOKEN> --name <NAME>
# Ctrl+C after it starts

# Edit the service file: replace YOUR_NAME with the tracker name
# e.g. sed -i 's/YOUR_NAME/Luna/' gps-bridge-connect.service
nano gps-bridge-connect.service

# Install service
mkdir -p ~/.config/systemd/user
cp gps-bridge-connect.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gps-bridge-connect
systemctl --user start gps-bridge-connect
```

**IMPORTANT:** The service file contains `--name YOUR_NAME` — you **must** replace `YOUR_NAME` with the actual tracker name (e.g. `Luna`) before installing. Without `--name`, the service will fail to start.

For multiple trackers, create separate service files for each name (e.g. `gps-bridge-connect-alice.service`).

---

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

1. **Bridge:** `gps-bridge connect --relay wss://yourdomain.com/relay --token <TOKEN> --name <NAME>`
2. **Phone app:** Settings → Relay server → add the custom URL

See https://github.com/luna61ouo/gps-relay for full documentation.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `gps-bridge connect` won't start | Check `gps-bridge pubkey` — if error, run `gps-bridge keygen` |
| No data after pairing | Verify token + pubkey match on both sides |
| App shows "sending" but no data | Token or pubkey mismatch — re-check both |
| Service keeps restarting | Run `gps-bridge connect --name <NAME>` manually to see error |
| Token lost | Check phone app Settings → Token field, or generate a new token and re-pair |
