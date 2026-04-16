# Discord Bridge Setup (mautrix-discord)

Step-by-step guide for setting up the Discord bridge in bot mode. This bridges
a Discord channel to a Matrix room bidirectionally, using a Discord bot account.

## Prerequisites

- Knarr deployed (Synapse running, cluster accessible)
- Admin access to a Discord server (to invite the bot)
- kubectl configured for the target cluster

## 1. Create the Discord Application and Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application**, name it (e.g. "Knarr Bridge")
3. In the left sidebar, click **Bot**
4. Under "Privileged Gateway Intents", enable:
   - **Message Content Intent** (required)
   - **Server Members Intent** (recommended)
   - **Presence Intent** (optional)
5. Click **Save Changes**
6. Click **Reset Token** and copy the token immediately — you cannot view it again

## 2. Generate the Bot Invite URL

1. In the left sidebar, click **OAuth2** → **URL Generator**
2. Under **Integration Type**, select **Guild Install** (default)
3. Under **Scopes**, check `bot`
4. Under **Bot Permissions**, check at minimum:
   - View Channels
   - Send Messages
   - Read Message History
   - Embed Links
   - Attach Files
   - Add Reactions
   - Use External Emojis
   - Manage Webhooks
5. Copy the **Generated URL** at the bottom
6. Open the URL, select the Discord server, authorize

## 3. Enable Discord Developer Mode (for channel IDs)

1. In Discord client: Settings → Advanced → toggle **Developer Mode** on
2. Right-click any channel you want to bridge → **Copy Channel ID**

## 4. Create Kubernetes Secrets

### Discord bot token secret

```bash
kubectl --context k3d-nordri-test create secret generic discord-bot-token \
  -n knarr --from-literal=token='YOUR_DISCORD_BOT_TOKEN'
```

### Appservice registration tokens

Generate AS and HS tokens (used by Synapse to trust the bridge):

```bash
AS_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
HS_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

Create the registration file — note the **single-quoted regex**. Double quotes
will break YAML parsing on the `\.` escape sequence:

```bash
cat > /tmp/discord-registration.yaml <<EOF
id: discord
url: http://mautrix-discord.knarr.svc.cluster.local:29334
as_token: "$AS_TOKEN"
hs_token: "$HS_TOKEN"
sender_localpart: discordbot
rate_limited: false
namespaces:
  users:
    - regex: '@discord_.*:knarr\.local'
      exclusive: true
  aliases:
    - regex: '#discord_.*:knarr\.local'
      exclusive: true
EOF

kubectl --context k3d-nordri-test create secret generic discord-bridge-registration \
  -n knarr --from-file=registration.yaml=/tmp/discord-registration.yaml
```

### Bridge config ConfigMap

The config needs the same AS/HS tokens. Write a `config.yaml` (see
`k8s/bridges/discord/` for a reference template) and create the ConfigMap:

```bash
kubectl --context k3d-nordri-test create configmap mautrix-discord-config \
  -n knarr --from-file=config.yaml=/tmp/mautrix-discord-config.yaml
```

Key config values (substitute the actual AS_TOKEN / HS_TOKEN):

```yaml
homeserver:
  address: http://synapse.knarr.svc.cluster.local:8008
  domain: knarr.local

appservice:
  address: http://mautrix-discord.knarr.svc.cluster.local:29334
  port: 29334
  id: discord
  bot:
    username: discordbot
    displayname: Discord Bridge Bot
  as_token: "..."
  hs_token: "..."
  database:
    type: sqlite3-fk-wal
    uri: file:/data/mautrix-discord.db?_txlock=immediate

bridge:
  permissions:
    "*": relay
    "knarr.local": user
    "@admin:knarr.local": admin
```

## 5. Update Synapse to Load the Appservice

Add to `homeserver.yaml` in the Synapse ConfigMap:

```yaml
app_service_config_files:
  - /bridges/discord/registration.yaml
```

Mount the registration secret into the Synapse pod at `/bridges/discord`:

```yaml
volumeMounts:
  - name: discord-bridge-reg
    mountPath: /bridges/discord
    readOnly: true
volumes:
  - name: discord-bridge-reg
    secret:
      secretName: discord-bridge-registration
```

## 6. Deploy the Bridge

Apply `k8s/bridges/discord/deployment.yaml`. The manifest uses an initContainer
to copy the ConfigMap into a writable volume (the bridge rewrites its config
on startup, so it needs a writable copy).

```bash
kubectl --context k3d-nordri-test apply -f k8s/bridges/discord/deployment.yaml
kubectl --context k3d-nordri-test rollout restart deploy/synapse -n knarr
```

Verify both are running:

```bash
kubectl --context k3d-nordri-test get pods -n knarr -l 'app in (synapse,mautrix-discord)'
kubectl --context k3d-nordri-test logs -n knarr -l app=mautrix-discord --tail=20
```

You should see "Bridge started!" in the bridge logs. If you see 401 errors on
`/_matrix/client/versions`, Synapse hasn't picked up the registration — check
for YAML parse errors (see Troubleshooting).

## 7. Log the Bridge Into Discord

The bridge needs to be told to connect to Discord using your bot token. This
is done via a Matrix management room.

From Element (logged in as admin):

1. **Start a DM with `@discordbot:knarr.local`** — this becomes the management room
2. Send the login command (replace with your actual bot token):

```
login-token bot YOUR_DISCORD_BOT_TOKEN
```

Expected reply: "Successfully logged in as @YourBotName" followed by
"Connecting to Discord as user ID ..."

**Alternative via the admin API** (if Element access isn't set up yet):

```bash
ADMIN_TOKEN=$(curl -s -X POST http://matrix.knarr.local/_matrix/client/v3/login \
  -H "Content-Type: application/json" \
  -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"admin"},"password":"YOUR_ADMIN_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create the management DM
ROOM_JSON=$(curl -s -X POST http://matrix.knarr.local/_matrix/client/v3/createRoom \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Discord Bridge","preset":"private_chat","invite":["@discordbot:knarr.local"],"is_direct":true}')
ROOM_ID=$(echo "$ROOM_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['room_id'])")

# Send the login command
ROOM_ENC=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$ROOM_ID'))")
TXN=$(date +%s%N)
curl -s -X PUT "http://matrix.knarr.local/_matrix/client/v3/rooms/${ROOM_ENC}/send/m.room.message/${TXN}" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"msgtype\":\"m.text\",\"body\":\"login-token bot $DISCORD_TOKEN\"}"
```

## 8. Bridge a Discord Channel

In the Matrix room you want to bridge (or a new one):

1. Invite `@discordbot:knarr.local` to the room
2. Send the bridge command with the Discord channel ID:

```
!discord bridge 1234567890123456789
```

Expected reply: "Room successfully bridged."

Test by sending a message in Matrix — it should appear in Discord with the
bot as the author. Test the reverse direction by sending in Discord — it
should appear in Matrix.

## Available Bridge Commands

Send these in the management room (no prefix needed) or any room (prefix with
`!discord`):

- **help** — list all commands
- **login-token bot \<token\>** — log in with a Discord bot token
- **logout** — disconnect from Discord
- **ping** — check connection to Discord
- **bridge \<channel_id\>** — bridge the current Matrix room to a Discord channel
- **unbridge** — unbridge the current room
- **create-portal \<channel_id\>** — create a new Matrix room bridged to a channel
- **guilds status** — list guilds the bot sees
- **guilds bridge \<guild_id\>** — bridge all channels in a guild

## Troubleshooting

### Synapse crashes with YAML error after applying registration

Symptom: `yaml.scanner.ScannerError: while scanning a double-quoted scalar,
found unknown escape character '.'`

Cause: The regex in `namespaces` was written with double quotes, which makes
YAML interpret `\.` as an escape sequence.

Fix: Use single quotes around the regex:
```yaml
- regex: '@discord_.*:knarr\.local'
```

### Bridge can't connect to Synapse (401 Invalid access token)

Cause: Synapse hasn't loaded the registration file (or the tokens mismatch
between the registration secret and the bridge config).

Fix: Verify the registration secret is mounted into the Synapse pod at
`/bridges/discord/registration.yaml`, and that `app_service_config_files`
references that path. Restart Synapse after any registration change.

### Bridge pod crashes with "unable to open database file"

Cause: The SQLite URI in `config.yaml` points to a path that isn't in a
writable volume.

Fix: Ensure the `data` volume (emptyDir or PVC) is mounted at the same path
referenced in `appservice.database.uri`. The deployment uses `/data` by
default.

### Bridge pod crashes with "permission denied" writing config

Cause: The bridge tries to rewrite its config on startup, but the config is
mounted read-only from a ConfigMap.

Fix: Use an initContainer to copy the config from the ConfigMap to a
writable volume before the bridge container starts. The deployment manifest
does this via a busybox initContainer.

### "Unknown command" when sending login-bot

Cause: mautrix-discord doesn't have a `login-bot` command — it's `login-token`
with `bot` as the first argument.

Fix: Use `login-token bot <token>` instead.

## Identity and Mention Mapping

Understanding how Discord and Matrix identities map to each other helps set
expectations and troubleshoot mention behavior.

### Discord → Matrix (single puppeting, automatic)

When any Discord user posts in a bridged channel, mautrix-discord creates a
"puppet" Matrix user for them automatically:

- Discord user `Cervator` (ID `12345`) speaks →
- Bridge creates `@discord_12345:knarr.local` with Cervator's display name and
  avatar synced from Discord →
- Message appears in the Matrix room as that puppet

No configuration or user action required. Puppets update when Discord profiles
change.

### Matrix → Discord (webhook mode)

Matrix messages are forwarded to Discord via webhooks when the bot has
`Manage Webhooks` permission and the bridge config has:

```yaml
prefix_webhook_messages: true
enable_webhook_avatars: true
```

A Matrix user named `Bob` posting in a bridged room appears on Discord as
"Bob" with Bob's Matrix avatar — but with a small "APP" or "Webhook" badge
next to the name. Close to native but visually distinct.

Alternative: without webhook permission, all Matrix messages appear from the
bot account prefixed with the sender's name, like `[Bob] Hello there`.

### Mention translation

| Direction | Behavior |
|-----------|----------|
| Discord user mentions another Discord user | Works natively — mention arrives in Matrix as a proper mention of that user's puppet |
| Discord user mentions a Matrix-only user | Matrix users don't have Discord IDs, so @-mentions can't target them from Discord. Text appears as plain text on both sides. |
| Discord user mentions `@<BotName>` (the bridge bot) | Resolves to the **Matrix user that logged the bridge in** (see below) |
| Matrix user mentions a Discord user | Bridge translates Matrix mention of the puppet to a native Discord mention — Discord user gets pinged properly |
| Matrix user mentions a Matrix-only user in a bridged room | Appears as plain text on Discord (no ping) |

### The "bridge bot" identity

When you run `login-token bot <token>`, the Matrix user who sends that command
becomes the "owner" of the Discord connection. From then on:

- The Discord bot account (`@YourBotName` on Discord) corresponds to that
  Matrix user
- When anyone on Discord pings the bot, the Matrix-side mention resolves to
  that owner user
- The bot appears in Discord messages as a normal bot account

**Recommendation: use a dedicated Matrix user for the bridge login**, separate
from your `admin` account. This avoids cross-wiring Discord pings of the bot
with your admin account.

Knarr's convention:

- Create `@knarr:knarr.local` (non-admin Matrix user, password stored
  securely, the Knarr avatar applied)
- Log the bridge in from `@knarr`'s management DM
- Now `@YourBotName` on Discord ↔ `@knarr:knarr.local` on Matrix

### Bridge permissions

The bridge config's `permissions` block controls who can run admin commands:

```yaml
permissions:
  "*": relay
  "knarr.local": user
  "@knarr:knarr.local": admin
  "@admin:knarr.local": admin
```

- `relay` — can see bridged messages (default for everyone)
- `user` — can be bridged to Discord, use basic commands
- `admin` — can run destructive/administrative bridge commands

Grant `admin` to the bridge's operator user(s). Regular community members only
need `user`.

### Double puppeting (optional, advanced)

By default, Matrix users show on Discord as webhooks. For a true "native
Discord user" appearance, a Matrix user can supply their own Discord token to
the bridge — the bridge then posts their messages as that Discord user
natively. Requires the user to:

1. Extract their own Discord user token from the Discord client
2. Run `login-token user <their-token>` in a DM with the bridge

Trade-offs: gives the cleanest bridging experience, but Discord considers
self-bots a TOS gray area. Use at your own risk, and only for dedicated power
users.

Knarr's default stance: bot mode only. Double puppeting is available for
power users who want it and accept the trade-offs.

## Persistent Storage (PVC)

**The bridge requires a PersistentVolumeClaim for its SQLite database.** Using
an `emptyDir` volume will lose all bridge state on every pod restart:

- Discord login (must re-run `login-token bot <token>`)
- Bridged channel mappings (must re-run `!discord bridge <channel-id>`)
- Puppet user state for all Discord users seen

The shipped deployment manifest (`k8s/bridges/discord/deployment.yaml`) uses
a 1Gi PVC for `/data`. Verify after deploying:

```bash
kubectl get pvc -n knarr mautrix-discord-data
```

Should show `STATUS: Bound`.

If you see the bridge re-initializing schemas from version 0 after a restart,
the PVC is not being used. Check the deployment's `volumes` section.

**Management rooms are stored in the bridge's DB, not Synapse's.** If the DB
is wiped (via emptyDir replacement or manual intervention), existing
management rooms become "unknown" to the bridge and need to be recreated. The
bridge can't retroactively discover rooms — create a fresh DM to establish a
new management room.

## Synapse Rate Limits

Synapse's default rate limits are calibrated for public servers defending
against abuse. For a private homeserver running operational flows (admin
actions, bridge logins, repeated API calls during development), they're too
aggressive.

Relaxed settings in the Synapse homeserver.yaml:

```yaml
rc_login:
  address:
    per_second: 10
    burst_count: 50
  account:
    per_second: 10
    burst_count: 50
  failed_attempts:
    per_second: 1
    burst_count: 10
rc_message:
  per_second: 100
  burst_count: 200
rc_admin_redaction:
  per_second: 100
  burst_count: 200
```

These are safe for a private homeserver with federation disabled. Revisit if
opening to federation or external users.

## References

- mautrix-discord docs: https://docs.mau.fi/bridges/go/discord/
- Discord developer portal: https://discord.com/developers/applications
- Matrix Application Service API: https://spec.matrix.org/latest/application-service-api/
