"""Step definitions for config reconciliation BDD tests."""

import subprocess
import sys

from pytest_bdd import given, when, then, parsers, scenarios

from src.admin.config_schema import load_config, validate_config

scenarios("../reconcile.feature")


def _run_cli(*args, config_path):
    return subprocess.run(
        [sys.executable, "-m", "src.admin.cli", "config", *args, "--config", config_path],
        capture_output=True,
        text=True,
        check=False,
    )


def _all_room_aliases(cfg):
    """Recursively collect every room alias from the loaded config."""
    aliases = []

    def walk(space):
        for room in space.rooms.values():
            aliases.append(f"#{room.alias}:{cfg.server_name}")
        for child in space.children.values():
            walk(child)

    for community in cfg.communities:
        for space in community.spaces.values():
            walk(space)
    return aliases


@given("a Matrix homeserver is running")
def check_homeserver(matrix_client):
    """Verify the homeserver is reachable."""
    token = matrix_client.get_token()
    assert token is not None


@given("a valid config with test rooms")
def valid_config(test_config_path):
    """Load and validate the test config."""
    cfg = load_config(test_config_path)
    validate_config(cfg)


@given("the config has been applied")
def apply_config(test_config_path):
    """Run apply to set up the baseline."""
    result = _run_cli("apply", config_path=test_config_path)
    assert result.returncode == 0, f"apply failed: {result.stdout}\n{result.stderr}"


@given("the configured rooms do not yet exist on the homeserver")
def teardown_rooms(matrix_client, test_config):
    """Delete every managed room so audit/apply start from a clean slate."""
    from urllib.parse import quote
    for alias in _all_room_aliases(test_config):
        room_id = matrix_client.resolve_alias(alias)
        if room_id:
            try:
                matrix_client._authed_request(
                    "DELETE",
                    f"/_synapse/admin/v2/rooms/{quote(room_id, safe='')}",
                    json={"purge": True, "message": "BDD teardown"},
                )
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass


@when("I run config validate", target_fixture="cli_result")
def run_validate(test_config_path):
    return _run_cli("validate", config_path=test_config_path)


@when("I run config audit", target_fixture="cli_result")
def run_audit(test_config_path):
    return _run_cli("audit", config_path=test_config_path)


@when("I run config apply", target_fixture="cli_result")
def run_apply(test_config_path):
    return _run_cli("apply", config_path=test_config_path)


@then(parsers.parse('it succeeds with "{text}"'))
def check_success(cli_result, text):
    assert cli_result.returncode == 0, f"stdout: {cli_result.stdout}\nstderr: {cli_result.stderr}"
    assert text in cli_result.stdout


@then('it reports "create" actions')
def check_create_actions(cli_result):
    assert "create" in cli_result.stdout


@then(parsers.parse("it exits with code {code:d}"))
def check_exit_code(cli_result, code):
    assert cli_result.returncode == code, (
        f"Expected {code}, got {cli_result.returncode}\n{cli_result.stdout}\n{cli_result.stderr}"
    )


@then(parsers.parse('room "{alias}" exists in Matrix'))
def check_room_exists(matrix_client, alias):
    room_id = matrix_client.resolve_alias(alias)
    assert room_id is not None, f"Room {alias} not found"


@then("the configured rooms exist in Matrix")
def check_all_rooms_exist(matrix_client, test_config):
    """Resolve every room alias declared in the config — derived from server_name."""
    missing = []
    for alias in _all_room_aliases(test_config):
        if matrix_client.resolve_alias(alias) is None:
            missing.append(alias)
    assert not missing, f"Rooms not found: {missing}"


@then("it reports zero drift")
def check_no_drift(cli_result):
    assert cli_result.returncode == 0, cli_result.stderr
    # Audit summary should not list create/invite/bridge/adopt actions.
    summary = cli_result.stdout.split("Audit:", 1)[-1]
    for op in ("create", "invite", "bridge", "adopt"):
        assert f" {op}" not in summary, (
            f"Drift detected — {op!r} appeared in summary: {summary}"
        )
