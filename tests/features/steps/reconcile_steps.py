"""Step definitions for config reconciliation BDD tests."""

import os
import subprocess

from pytest_bdd import given, when, then, parsers, scenarios

from src.admin.client import MatrixAdminClient
from src.admin.config_schema import load_config, validate_config
from src.admin.reconciler import Reconciler

scenarios("../reconcile.feature")


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
def apply_config(matrix_client, test_config_path):
    """Run apply to set up the baseline."""
    cfg = load_config(test_config_path)
    validate_config(cfg)
    reconciler = Reconciler(matrix_client, cfg)
    report = reconciler.apply()
    assert not report.errors


@when("I run config validate", target_fixture="cli_result")
def run_validate(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "validate", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@when("I run config audit", target_fixture="cli_result")
def run_audit(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "audit", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@when("I run config apply", target_fixture="cli_result")
def run_apply(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "apply", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@then(parsers.parse('it succeeds with "{text}"'))
def check_success(cli_result, text):
    assert cli_result.returncode == 0, f"stdout: {cli_result.stdout}\nstderr: {cli_result.stderr}"
    assert text in cli_result.stdout


@then('it reports "create" actions')
def check_create_actions(cli_result):
    assert "create" in cli_result.stdout


@then(parsers.parse("it exits with code {code:d}"))
def check_exit_code(cli_result, code):
    assert cli_result.returncode == code, f"Expected {code}, got {cli_result.returncode}\n{cli_result.stdout}\n{cli_result.stderr}"


@then(parsers.parse('room "{alias}" exists in Matrix'))
def check_room_exists(matrix_client, alias):
    room_id = matrix_client.resolve_alias(alias)
    assert room_id is not None, f"Room {alias} not found"


@then("it reports zero drift")
def check_no_drift(cli_result):
    assert cli_result.returncode == 0
