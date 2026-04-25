"""Shared fixtures for BDD integration tests."""

import os
import pytest

from src.admin.client import MatrixAdminClient


@pytest.fixture
def matrix_client():
    """Create a client connected to the real Synapse on k3d."""
    homeserver = os.environ.get("KNARR_HOMESERVER", "http://matrix.knarr.local")
    user = os.environ.get("KNARR_ADMIN_USER", "admin")
    password = os.environ.get("KNARR_ADMIN_PASSWORD")
    if not password:
        pytest.skip("KNARR_ADMIN_PASSWORD not set — skipping integration test")
    return MatrixAdminClient(homeserver, user, password)


@pytest.fixture
def test_config_path():
    """Path to the test config."""
    return "config/knarr.yaml"
