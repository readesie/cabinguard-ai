# Tesla Fleet API Setup — CabinGuard AI

Getting your Model 3 connected is a one-time setup. Here's exactly what you need.

---

## Step 1 — Register a Tesla Developer Application

1. Go to [developer.tesla.com](https://developer.tesla.com) and sign in with your Tesla account.
2. Create a new application. Name it anything (e.g. "CabinGuard AI").
3. Set the origin uri to `http://localhost:8000`.
4. Set the redirect and returned uris to `https://auth.tesla.com/void/callback` for local/CLI testing.
5. Request these OAuth scopes:
   - `vehicle_cmds` — required for window vent/close commands
   - `vehicle_device_data` — required to read vehicle state and temperatures

Tesla manually reviews Fleet API applications. Approval typically takes 1–3 business days.

---

## Step 2 — Set Environment Variables

```bash
#if linux/unix export TESLA_CLIENT_ID=your_application_client_id # e.g. found in Credentials & APIs tab of created app on developer.tesla.com
#if linux/unix export TESLA_CLIENT_SECRET=your_application_client_secret # e.g. found in Credentials & APIs tab of created app on developer.tesla.com (likely gibberish like a password)
#if linux/unix export TESLA_MODEL3_VIN=5YJ3E1EAXNF......   # e.g. found in the Tesla app → Settings → About

#if linux/unix Add these to your `~/.bashrc` or `~/.zshrc` so they persist across sessions.
```

```windows
#if windows Press Win + R → type sysdm.cpl
#if windows Advanced tab → Environment Variables
#if windows Under User variables, click New for each
#if windows TESLA_CLIENT_ID = your client id
#if windows TESLA_CLIENT_SECRET = your client secret
#if windows TESLA_MODEL3_VIN = your VIN
#if windows Restart terminal
```
---

## Step 3 — Update config.yaml

```yaml
If hardcoded...
tesla:
  client_id: YOUR_CLIENT_ID      
  client_secret: YOUR_SECRET
  simulated: false               # switch from true to false to go live

If using windows env vars...
tesla:
  client_id: ${TESLA_CLIENT_ID}
  client_secret: ${TESLA_CLIENT_SECRET}
  simulated: false               # switch from true to false to go live


```

---

## Step 4 — Test with the CLI

```bash
# Simulated mode first (no real calls)
SIMULATE=1 python tests/tesla/live_test_cli.py

# Then live
python tests/tesla/live_test_cli.py
```

Start with option **1** (vehicle state) to confirm connectivity before
sending any window commands.

---

## Step 5 — Run the Integration Tests

```bash
TESLA_LIVE_TEST=1 pytest tests/tesla/test_tesla_integration.py -v -s
```

Run `test_vent_windows` and `test_close_windows` individually first before
running the full cycle.

---

## Model 3 Window API Notes

The Tesla Fleet API `window_control` command supports two values:

| Command | Effect |
|---|---|
| `vent` | Opens all windows approximately 1 inch (3cm) |
| `close` | Closes all windows fully |

**Known constraints:**
- The car must be in `PARK` to accept window commands.
- The car must be awake — CabinGuard AI handles the wake sequence automatically.
- If a window detects an obstruction, the command returns `result: false` with reason `window_obstruction`. CabinGuard AI logs this and retries on the next alert cycle.
- Rear windows on the Model 3 only vent; they do not have full open/close control via the API (this is a hardware limitation, not a software one).

---

## Vehicle State — Window Position Codes

| Code | Meaning |
|---|---|
| `0` | Closed |
| `1` | Venting (approximately 1 inch open) |
| `2` | Fully open |

These are returned in `vehicle_state.fd_window`, `fp_window`, `rd_window`, `rp_window`.

---

## Troubleshooting

**401 Unauthorized** — Client credentials are wrong or expired. Regenerate in the Tesla developer portal.

**408 / vehicle offline** — Wake sequence failed. Increase `max_attempts` in config or check if the car has connectivity (parking garage dead zones are common).

**`result: false, reason: "not_allowed_in_current_state"`** — Car is not in PARK, or climate/venting is active. CabinGuard AI will log and retry.

**`result: false, reason: "window_obstruction"`** — Something is blocking the window. Check the car manually.
