# Knarr CLI

Operational commands for the Matrix homeserver and Discord bridge. Replaces
multi-line curl incantations with one-liners.

## Two Ways to Use

### As a CLI (for humans and AI agents)

```bash
# Via the bash wrapper (sources knarr.env automatically)
./scripts/knarr room create "my-room" --invite "@cervator:knarr.local"

# Or directly via Python
python3 -m src.admin.cli room create "my-room"
```

### As a Python library (for apps and services)

```python
from src.admin.client import MatrixAdminClient
from src.admin.bridge import BridgeManager

# Create a client
client = MatrixAdminClient(
    homeserver="http://matrix.knarr.local",
    admin_user="admin",
    admin_password="...",
)

# Room operations
room_id = client.create_room("announcements", invite=["@bob:knarr.local"])
client.send_message(room_id, "Hello from Python!")
messages = client.get_messages(room_id, limit=10)

# Bridge operations
bridge_client = MatrixAdminClient("http://matrix.knarr.local", "knarr", "...")
mgr = BridgeManager(bridge_client, management_room="!mgmt:knarr.local")
mgr.login_bot("discord-bot-token")
mgr.create_and_bridge(
    channel_id="1342947610008485921",
    room_name="my-channel (Discord)",
    invite=["@cervator:knarr.local"],
)
```

The same `MatrixAdminClient` and `BridgeManager` classes power both the CLI
and any future web admin UI or automation service.

## Setup

1. Copy `knarr.env.template` to `knarr.env` and fill in credentials:

```bash
cp knarr.env.template knarr.env
# Edit knarr.env with your values
```

2. The bash wrapper sources `knarr.env` automatically. For direct Python use,
   export the environment variables or load them in your code.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KNARR_HOMESERVER` | `http://matrix.knarr.local` | Matrix homeserver URL |
| `KNARR_ADMIN_USER` | `admin` | Admin username for room/user ops |
| `KNARR_ADMIN_PASSWORD` | *(required)* | Admin password |
| `KNARR_BRIDGE_USER` | `knarr` | Bridge operator username |
| `KNARR_BRIDGE_PASSWORD` | *(required for bridge ops)* | Bridge operator password |
| `KNARR_MANAGEMENT_ROOM` | *(required for bridge ops)* | Room ID of the bridge management DM |
| `DISCORD_TOKEN` | *(required for bridge login)* | Discord bot token |

## Commands

### Room Operations

```bash
# Create a room
knarr room create "room-name" [--topic "..."] [--invite @user:knarr.local] [--public]

# Invite a user
knarr room invite "!roomid:knarr.local" "@user:knarr.local"

# Send a message
knarr room send "!roomid:knarr.local" "Hello world"

# View recent messages
knarr room messages "!roomid:knarr.local" [--limit 20]

# List rooms the admin user has joined
knarr room list
```

### User Operations

```bash
# Create a user (prompts for password)
knarr user create username [--admin]

# Set display name
knarr user set-displayname "@user:knarr.local" "Display Name"

# Set avatar from image file
knarr user set-avatar "@user:knarr.local" ./avatar.png
```

Note: User creation uses Synapse's admin registration API, which requires
the shared secret. If that fails, the CLI outputs the equivalent `kubectl
exec` command as a fallback.

### Bridge Operations

```bash
# Check bridge status
knarr bridge ping

# Log bridge into Discord
knarr bridge login-bot [--token TOKEN]  # or set DISCORD_TOKEN env var

# Bridge a Discord channel (creates room + relay webhook in one step)
knarr bridge bridge-channel CHANNEL_ID \
  --room "channel-name (Discord)" \
  --invite "@cervator:knarr.local" \
  [--replace]

# Bridge into an existing room
knarr bridge bridge-channel CHANNEL_ID --room-id "!existing:knarr.local"

# Disconnect from Discord
knarr bridge logout
```

The `bridge-channel --room` command does three things in one call:
1. Creates a Matrix room with the given name and invites
2. Sends `!discord bridge <channel-id>` to bridge the room
3. Sends `!discord set-relay --create` to enable Matrix-to-Discord messaging

### Config Reconciliation

```bash
# Validate config syntax (no Matrix connection needed)
knarr config validate [--config config/knarr.yaml]

# Audit: dry-run, report what would change
knarr config audit [--config config/knarr.yaml]

# Apply: converge live state to match config
knarr config apply [--config config/knarr.yaml] [--allow-missing-secrets]
```

Config files live in `config/`:
- `config/knarr.yaml` — index file (server name, users, secrets, community references)
- `config/test.yaml` — per-community room topology, bridges, watchers

#### Exit codes

| Command | Code | Meaning |
|---------|------|---------|
| `validate` | 0 | Config is valid |
| `validate` | 1 | Config or file error |
| `audit` | 0 | Config is valid and Matrix matches it (no drift) |
| `audit` | 1 | Config or file error |
| `audit` | 2 | Drift detected — apply would make changes |
| `apply` | 0 | Applied successfully (or no changes needed) |
| `apply` | 1 | Config error, missing secrets, or apply failed |

`audit`'s exit code 2 is intentional for CI use: a non-zero exit means
"there is drift", letting you wire it into a periodic check that fails
loudly when production drifts from Git.

`apply` aborts with exit 1 when secrets referenced by the config aren't
set in the environment — preventing partial mutations. Pass
`--allow-missing-secrets` to override.

### Utility

```bash
# Get an admin access token (for debugging with curl/httpie)
knarr admin-token
```

## Architecture

```
scripts/knarr              (bash wrapper — sources knarr.env, delegates to Python)
    ↓
src/admin/cli.py           (Click CLI — parses args, calls client/bridge)
    ↓
src/admin/client.py        (MatrixAdminClient — token cache, room/user ops)
src/admin/bridge.py        (BridgeManager — Discord bridge commands via Matrix DM)
```

The CLI is a thin presentation layer. All logic lives in `client.py` and
`bridge.py`, which are importable by any Python code — a future FastAPI admin
UI, an automation script, or the router bot itself.

## Testing

```bash
python3 -m pytest tests/ -v
```

Tests use httpx mock transport (no live server needed). Bridge manager tests
patch `time.sleep` to avoid waiting for real command responses.

## Naming Convention for Test Rooms

When bridging Discord channels for testing, use a suffix to distinguish test
rooms from production:

```bash
knarr bridge bridge-channel CHANNEL_ID --room "#channel-name [test-1]"
```

Increment the suffix when re-bridging (`[test-2]`, `[test-3]`, etc.). This
avoids confusion when Matrix accumulates multiple rooms pointing at the same
Discord channel across bridge rebuilds.
