"""`knarr publish`: what is checked before any credential is read."""

from pathlib import Path

from click.testing import CliRunner

from src.admin.cli import cli

# The committed config: it declares mtl-matrix in community/mtl.
_CONFIG = Path(__file__).resolve().parents[1] / "config" / "knarr.yaml"


def _run(tmp_path, monkeypatch, *args):
    monkeypatch.delenv("KNARR_BRIDGE_PASSWORD", raising=False)
    return CliRunner().invoke(cli, [
        "publish", "--config", str(_CONFIG),
        "--store", str(tmp_path / "results.json"), "--body", "hi", *args,
    ])


def test_wrong_scope_is_refused_before_credentials_are_read(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch,
                  "--scope", "group/pta", "--targets", "mtl-matrix")
    assert result.exit_code == 1
    assert "scope mismatch" in result.output
    assert "KNARR_BRIDGE_PASSWORD" not in result.output


def test_dry_run_needs_no_password(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, "--scope", "community/mtl",
                  "--targets", "mtl-matrix", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "published 0" in result.output


def test_real_send_still_requires_a_password(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch,
                  "--scope", "community/mtl", "--targets", "mtl-matrix")
    assert result.exit_code == 1
    assert "KNARR_BRIDGE_PASSWORD" in result.output


def test_targets_that_name_nothing_are_rejected(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, "--scope", "community/mtl",
                  "--targets", " , ", "--dry-run")
    assert result.exit_code == 1
    assert "no publisher ids" in result.output
