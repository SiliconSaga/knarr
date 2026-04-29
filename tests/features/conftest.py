"""Shared fixtures for BDD integration tests."""

from pathlib import Path

import pytest

from src.admin.cli import client_from_env
from src.admin.config_schema import load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def matrix_client():
    """Create a client connected to the real Synapse on k3d."""
    return client_from_env(
        "KNARR_ADMIN_USER",
        "KNARR_ADMIN_PASSWORD",
        "admin",
        lambda: pytest.skip("KNARR_ADMIN_PASSWORD not set — skipping integration test"),
    )


@pytest.fixture
def test_config_path():
    """Absolute path to the test config — robust to alternative pytest invocations."""
    return str(_REPO_ROOT / "config" / "knarr.yaml")


@pytest.fixture
def test_config(test_config_path: str):
    """Load the test config so steps can derive aliases dynamically.

    Depends on ``test_config_path`` so the path lives in exactly one place.
    """
    return load_config(test_config_path)
