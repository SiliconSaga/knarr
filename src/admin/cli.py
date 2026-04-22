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

from .client import MatrixAdminClient
from .bridge import BridgeManager


def get_client() -> MatrixAdminClient:
    homeserver = os.environ.get("KNARR_HOMESERVER", "http://matrix.knarr.local")
    user = os.environ.get("KNARR_ADMIN_USER", "admin")
    password = os.environ.get("KNARR_ADMIN_PASSWORD")
    if not password:
        click.echo("Error: KNARR_ADMIN_PASSWORD is required.", err=True)
        sys.exit(1)
    return MatrixAdminClient(homeserver, user, password)


def get_bridge_client() -> MatrixAdminClient:
    homeserver = os.environ.get("KNARR_HOMESERVER", "http://matrix.knarr.local")
    user = os.environ.get("KNARR_BRIDGE_USER", "knarr")
    password = os.environ.get("KNARR_BRIDGE_PASSWORD")
    if not password:
        click.echo("Error: KNARR_BRIDGE_PASSWORD is required.", err=True)
        sys.exit(1)
    return MatrixAdminClient(homeserver, user, password)


def get_bridge_manager() -> BridgeManager:
    management_room = os.environ.get("KNARR_MANAGEMENT_ROOM")
    if not management_room:
        click.echo("Error: KNARR_MANAGEMENT_ROOM is required.", err=True)
        sys.exit(1)
    return BridgeManager(get_bridge_client(), management_room)


@click.group()
def cli():
    """Knarr — operational commands for Matrix homeserver and bridges."""
    pass


# --- Room commands ---

@cli.group()
def room():
    """Matrix room operations."""
    pass


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
    pass


@user.command("create")
@click.argument("username")
@click.option("--password", "-p", prompt=True, hide_input=True, help="User password")
@click.option("--admin", "is_admin", is_flag=True, help="Grant admin privileges")
def user_create(username, password, is_admin):
    """Create a Matrix user via Synapse admin API."""
    client = get_client()
    try:
        user_id = client.register_user(username, password, admin=is_admin)
        click.echo(f"Created: {user_id}")
    except NotImplementedError as e:
        click.echo(f"Note: {e}", err=True)
        click.echo("Use: kubectl exec -n knarr deploy/synapse -- "
                    f"register_new_matrix_user -c /config/homeserver.yaml "
                    f"-u {username} -p <password> "
                    f"{'--admin' if is_admin else '--no-admin'} http://localhost:8008")


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
    pass


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
        result_room = mgr.create_and_bridge(
            channel_id=channel_id,
            room_name=room_name,
            invite=list(invite) if invite else None,
            replace=replace,
        )
        click.echo(f"Created and bridged: {result_room}")
    else:
        mgr.bridge_channel(room_id, channel_id, replace=replace)
        click.echo(f"Bridged {room_id} to Discord channel {channel_id}")


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


# --- Token command ---

@cli.command("admin-token")
def admin_token():
    """Get a Matrix admin access token (for debugging)."""
    client = get_client()
    token = client.get_token()
    click.echo(token)


if __name__ == "__main__":
    cli()
