# iRacing Standalone Tracker — Full V1 Internet Stack

This pack gives you a full first-pass internet-capable stack:

- `relay_server.py` — cloud relay / remote dashboard server
- `tracker_publisher.py` — driver-side publisher
- `config.example.json` — local publisher config
- `requirements.txt`
- Windows batch files for fast startup

## Architecture

Driver PC:
- reads iRacing locally
- builds a cleaned strategy payload
- publishes it every second to the relay

Cloud relay:
- stores latest session state
- serves remote JSON API
- serves a browser dashboard

Remote viewers:
- open a URL and watch live state from anywhere

## Quick local test (no internet, no iRacing)

### 1. Install Python
Use Python 3.11 or 3.12.

### 2. Install packages
In this folder:
```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Start the relay
```bash
py relay_server.py --host 0.0.0.0 --port 8000
```

### 4. Start the publisher in mock mode
`config.example.json` defaults to `"mode": "mock"`, so just run:
```bash
py tracker_publisher.py --config config.example.json
```

### 5. Open the dashboard
Browser:
```text
http://127.0.0.1:8000/session/osr-b12-2026?token=change-me-read-token
```

If you leave read auth disabled on the server, you can omit the token.

## Using simple auth

The relay supports optional environment-variable auth.

### Relay
Windows Command Prompt example:
```bat
set WRITE_TOKEN=change-me-write-token
set READ_TOKEN=change-me-read-token
py relay_server.py --host 0.0.0.0 --port 8000
```

### Publisher
Keep the same write token in `config.example.json`:
```json
"relay": {
  "update_url": "https://your-host/api/update",
  "write_token": "change-me-write-token"
}
```

### Viewer
Open:
```text
https://your-host/session/osr-b12-2026?token=change-me-read-token
```

## Moving from mock mode to iRacing mode

In `config.example.json` change:
```json
"mode": "iracing"
```

Then the publisher will:
- wait for iRacing
- connect when the sim is running
- publish live state

## Important notes

- This is a v1 stack.
- The relay is intentionally dumb: it stores and serves state.
- The strategy logic lives on the driver PC.
- This is the right design for reliability and easier future upgrades.

## What this version already includes

- remote sync over internet
- read/write tokens
- browser dashboard
- waiting for iRacing instead of quitting
- mock test mode
- 4-tyre delta vs fuel-only logic
- green-flag configurable start lap
- simple stop recommendation fields

## What you will likely add next

- persistent multi-user auth
- WebSocket updates
- proper driver handoff rules
- richer pit/fuel learning
- rulesets and compliance engine
- better in-car fuel sourcing and shared telemetry publishing
