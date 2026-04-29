"""Knarr CLI — operational commands for Matrix homeserver and bridges.

Usage:
    python -m src.admin.cli [OPTIONS] COMMAND [ARGS...]

Configuration via environment variables:
    KNARR_HOMESERVER       Matrix homeserver URL (default: http://matrix.knarr.local)
    KNARR_ADMIN_USER       Admin username (default: admin)
    KNARR_ADMIN_PASSWORD   Admin password (required for most commands)
    KNARR_BRIDGE_USER      Bridge operator username (default: knarr)
    KNARR_BRIDGE_PASSWORD  Bridge operator password
    KNARR_MANAGEMENT_ROOM  Bridge management DM room ID
    DISCORD_TOKEN          Discord bot token (for bridge login)
"""

import os
import sys

import click

from .bridge import BridgeManager
from .client import MatrixAdminClient
from .config_schema import ConfigError, load_config, validate_config
from .reconciler import Reconciler

_ACTION_ICONS = {
    "create": "+",
    "skip": "=",
    "invite": ">",
    "bridge": "~",
    "noop": ".",
    "adopt": "!",
    "error": "X",
}


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _walk_rooms(space):
    """Yield every room in a space, recursing through nested children."""
    yield from space.rooms.values()
    for child in space.children.values():
        yield from _walk_rooms(child)


def client_from_env(
    user_var: str,
    password_var: str,
    default_user: str,
    on_missing_password,
) -> MatrixAdminClient:
    """Construct a MatrixAdminClient from environment variables.

    ``on_missing_password`` is called when the password env var is unset and
    decides what to do (CLI: print error + sys.exit; tests: pytest.skip).
    Centralised so a future env var change lands in one place.
    """
    homeserver = os.environ.get("KNARR_HOMESERVER", "http://matrix.knarr.local")
    user = os.environ.get(user_var, default_user)
    password = os.environ.get(password_var)
    if not password:
        on_missing_password()
    return MatrixAdminClient(homeserver, user, password)


def _abort_missing(env_var: str):
    def _abort():
        click.echo(f"Error: {env_var} is required.", err=True)
        sys.exit(1)
    return _abort


def get_client() -> MatrixAdminClient:
    return client_from_env(
        "KNARR_ADMIN_USER", "KNARR_ADMIN_PASSWORD", "admin",
        _abort_missing("KNARR_ADMIN_PASSWORD"),
    )


def get_bridge_client() -> MatrixAdminClient:
    return client_from_env(
        "KNARR_BRIDGE_USER", "KNARR_BRIDGE_PASSWORD", "knarr",
        _abort_missing("KNARR_BRIDGE_PASSWORD"),
    )


def get_bridge_manager() -> BridgeManager:
    management_room = os.environ.get("KNARR_MANAGEMENT_ROOM")
    if not management_room:
        click.echo("Error: KNARR_MANAGEMENT_ROOM is required.", err=True)
        sys.exit(1)
    return BridgeManager(get_bridge_client(), management_room)


@click.group()
def cli():
    """Knarr — operational commands for Matrix homeserver and bridges."""


# --- Room commands ---

@cli.group()
def room():
    """Matrix room operations."""


@room.command("create")
@click.argument("name")
@click.option("--topic", "-t", default="", help="Room topic")
@click.option("--invite", "-i", multiple=True, help="User IDs to invite (repeatable)")
@click.option("--public", "is_public", is_flag=True, help="Create as public room")
def room_create(name, topic, invite, is_public):
    """Create a Matrix room."""
    client = get_client()
    invite_list = list(invite) if invite else None
    room_id = client.create_room(name, topic=topic, invite=invite_list, private=not is_public)
    click.echo(f"Created: {room_id}")


@room.command("invite")
@click.argument("room_id")
@click.argument("user_id")
def room_invite(room_id, user_id):
    """Invite a user to a room."""
    client = get_client()
    client.invite(room_id, user_id)
    click.echo(f"Invited {user_id} to {room_id}")


@room.command("send")
@click.argument("room_id")
@click.argument("message")
def room_send(room_id, message):
    """Send a message to a room."""
    client = get_client()
    event_id = client.send_message(room_id, message)
    click.echo(f"Sent: {event_id}")


@room.command("messages")
@click.argument("room_id")
@click.option("--limit", "-n", default=10, help="Number of messages")
def room_messages(room_id, limit):
    """Show recent messages in a room."""
    client = get_client()
    messages = client.get_messages(room_id, limit=limit)
    for msg in reversed(messages):
        sender = msg.get("sender", "?")
        body = msg.get("content", {}).get("body", "")
        click.echo(f"  {sender}: {body}")


@room.command("list")
def room_list():
    """List rooms the admin user has joined."""
    client = get_client()
    rooms = client.get_joined_rooms()
    for r in rooms:
        click.echo(f"  {r}")


# --- User commands ---

@cli.group()
def user():
    """Matrix user operations."""


@user.command("create")
@click.argument("username")
@click.option("--password", "-p", prompt=True, hide_input=True, help="User password")
@click.option("--admin", "is_admin", is_flag=True, help="Grant admin privileges")
def user_create(username, password, is_admin):
    """Create a Matrix user via Synapse admin API."""
    import httpx
    client = get_client()
    server_name = os.environ.get("KNARR_SERVER_NAME", "knarr.local")
    try:
        user_id = client.register_user(username, password, admin=is_admin, server_name=server_name)
        click.echo(f"Created: {user_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            click.echo(f"Admin API rejected request ({e.response.status_code}). Fallback:", err=True)
            click.echo("kubectl exec -n knarr deploy/synapse -- "
                       f"register_new_matrix_user -c /config/homeserver.yaml "
                       f"-u {username} -p <password> "
                       f"{'--admin' if is_admin else '--no-admin'} http://localhost:8008")
        else:
            raise


@user.command("set-displayname")
@click.argument("user_id")
@click.argument("display_name")
def user_set_displayname(user_id, display_name):
    """Set a user's display name."""
    client = get_client()
    client.set_display_name(user_id, display_name)
    click.echo(f"Set display name for {user_id} to '{display_name}'")


@user.command("set-avatar")
@click.argument("user_id")
@click.argument("image_path", type=click.Path(exists=True))
def user_set_avatar(user_id, image_path):
    """Upload and set a user's avatar."""
    client = get_client()
    client.set_avatar(user_id, image_path)
    click.echo(f"Set avatar for {user_id} from {image_path}")


# --- Bridge commands ---

@cli.group()
def bridge():
    """Discord bridge operations."""


@bridge.command("login-bot")
def bridge_login_bot():
    """Log the bridge into Discord with a bot token.

    Reads the token from DISCORD_TOKEN environment variable (set in knarr.env).
    Never pass tokens as command-line arguments — they leak to shell history.
    """
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        click.echo("Error: Set DISCORD_TOKEN in knarr.env or environment.", err=True)
        sys.exit(1)
    mgr = get_bridge_manager()
    result = mgr.login_bot(token)
    click.echo(result)


@bridge.command("bridge-channel")
@click.argument("channel_id")
@click.option("--room", "room_name", default=None, help="Create a new room with this name")
@click.option("--room-id", default=None, help="Bridge into existing room")
@click.option("--invite", "-i", multiple=True, help="Users to invite (repeatable)")
@click.option("--replace", is_flag=True, help="Replace existing bridge mapping")
def bridge_channel(channel_id, room_name, room_id, invite, replace):
    """Bridge a Discord channel to a Matrix room."""
    if room_name and room_id:
        click.echo("Error: --room and --room-id are mutually exclusive.", err=True)
        sys.exit(1)
    if not room_name and not room_id:
        click.echo("Error: Provide --room <name> to create a new room, or --room-id to bridge existing.", err=True)
        sys.exit(1)
    mgr = get_bridge_manager()
    if room_name:
        room_id, bridge_result = mgr.create_and_bridge(
            channel_id=channel_id,
            room_name=room_name,
            invite=list(invite) if invite else None,
            replace=replace,
        )
        click.echo(f"Created and bridged: {room_id}")
        if "WARNING" in bridge_result:
            click.echo(bridge_result, err=True)
    else:
        bridge_result = mgr.bridge_channel(room_id, channel_id, replace=replace)
        click.echo(f"Bridged {room_id} to Discord channel {channel_id}")
        if "WARNING" in bridge_result:
            click.echo(bridge_result, err=True)


@bridge.command("ping")
def bridge_ping():
    """Check bridge connection to Discord."""
    mgr = get_bridge_manager()
    result = mgr.ping()
    click.echo(result)


@bridge.command("logout")
def bridge_logout():
    """Disconnect bridge from Discord."""
    mgr = get_bridge_manager()
    result = mgr.logout()
    click.echo(result)


# --- Config commands ---

@cli.group()
def config():
    """GitOps config reconciliation."""


@config.command("validate")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
def config_validate(config_path):
    """Validate config syntax without touching Matrix."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"File not found: {e}", err=True)
        sys.exit(1)

    def _count_space(space):
        s, r, b, w = 1, len(space.rooms), 0, 0
        for room in space.rooms.values():
            if room.bridge:
                # Count one per bridge type so the validate output matches
                # what reconcile/apply does (one action per bridge type).
                b += len(room.bridge)
            if room.watchers:
                w += len(room.watchers)
        for child in space.children.values():
            cs, cr, cb, cw = _count_space(child)
            s += cs
            r += cr
            b += cb
            w += cw
        return s, r, b, w

    space_count = room_count = bridge_count = watcher_count = 0
    for c in cfg.communities:
        for s in c.spaces.values():
            cs, cr, cb, cw = _count_space(s)
            space_count += cs
            room_count += cr
            bridge_count += cb
            watcher_count += cw

    click.echo(
        f"Config valid: {_plural(len(cfg.communities), 'community', 'communities')}, "
        f"{_plural(space_count, 'space', 'spaces')}, "
        f"{_plural(room_count, 'room', 'rooms')}, "
        f"{_plural(bridge_count, 'bridge', 'bridges')}, "
        f"{_plural(watcher_count, 'watcher', 'watchers')}"
    )


@config.command("audit")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
def config_audit(config_path):
    """Dry-run: report what would change without making changes."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except (ConfigError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    client = get_client()
    reconciler = Reconciler(client, cfg)
    report = reconciler.diff()

    click.echo(f"Loading config: {config_path} ({_plural(len(cfg.communities), 'community', 'communities')})")
    click.echo("Reading Matrix state...\n")

    for action in report.actions:
        click.echo(f"  [{_ACTION_ICONS.get(action.operation, '?')}] {action.resource:<28} {action.operation:<8} {action.details}")

    click.echo(f"\nAudit: {report.summary()}")
    if report.has_drift:
        sys.exit(2)


@config.command("apply")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
@click.option("--allow-missing-secrets", is_flag=True,
              help="Proceed even when configured secrets aren't set in the environment")
def config_apply(config_path, allow_missing_secrets):
    """Apply config: converge live state to match desired state."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except (ConfigError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Validate secrets are available — fail by default to avoid partial mutations
    missing = [
        (name, env_var)
        for name, env_var in cfg.secrets.items()
        if not os.environ.get(env_var)
    ]
    if missing:
        for name, env_var in missing:
            click.echo(f"Missing secret '{name}' ({env_var}) in environment", err=True)
        if not allow_missing_secrets:
            click.echo("Aborting. Re-run with --allow-missing-secrets to proceed anyway.", err=True)
            sys.exit(1)

    client = get_client()
    bridge_mgr = None
    mgmt_room = os.environ.get("KNARR_MANAGEMENT_ROOM")
    has_bridge_config = any(
        room.bridge
        for community in cfg.communities
        for space in community.spaces.values()
        for room in _walk_rooms(space)
    )
    if has_bridge_config and not mgmt_room:
        # Without KNARR_MANAGEMENT_ROOM the reconciler silently no-ops bridge
        # actions; with bridge config present that's a partial converge with
        # exit 0, which is worse than failing fast.
        click.echo(
            "Error: KNARR_MANAGEMENT_ROOM is required when bridge config is present.",
            err=True,
        )
        sys.exit(1)
    if mgmt_room:
        bridge_mgr = get_bridge_manager()

    reconciler = Reconciler(client, cfg, bridge_manager=bridge_mgr)

    click.echo(f"Loading config: {config_path} ({_plural(len(cfg.communities), 'community', 'communities')})")
    click.echo("Reading Matrix state...\n")

    report = reconciler.apply()

    for action in report.actions:
        click.echo(f"  [{_ACTION_ICONS.get(action.operation, '?')}] {action.resource:<28} {action.operation:<8} {action.details}")

    if report.errors:
        click.echo("\nErrors:", err=True)
        for error in report.errors:
            click.echo(f"  {error}", err=True)

    click.echo(f"\nApplied: {report.summary()}")
    if report.errors:
        sys.exit(1)


# --- Token command ---

@cli.command("admin-token")
def admin_token():
    """Get a Matrix admin access token (for debugging)."""
    client = get_client()
    token = client.get_token()
    click.echo(token)


if __name__ == "__main__":
    cli()
