import pytest

from src.admin.config_schema import PublisherConfig
from src.publisher.factory import UnsupportedPublisher, build_publishers


def _cfg(pid="p", platform="matrix", surface="room"):
    return PublisherConfig(
        id=pid, platform=platform, surface=surface, access_path="api",
        scope="community/mtl", target="!room:knarr.local",
    )


def test_builds_a_matrix_publisher():
    built = build_publishers([_cfg()], client=object())
    assert built[0].platform == "matrix"
    assert built[0].id == "p"


def test_builds_a_discord_publisher():
    built = build_publishers([_cfg("d", "discord", "channel")], client=object())
    assert built[0].platform == "discord"


def test_unsupported_pair_raises_naming_the_publisher():
    with pytest.raises(UnsupportedPublisher, match="p2"):
        build_publishers([_cfg("p2", "instagram", "feed")], client=object())


def test_credentials_ref_is_rejected_not_ignored():
    cfg = _cfg("c")
    cfg.credentials_ref = {"secret_name": "s", "secret_key": "k"}
    with pytest.raises(UnsupportedPublisher, match="credentials_ref"):
        build_publishers([cfg], client=object())


def test_builds_several():
    built = build_publishers(
        [_cfg("a"), _cfg("b", "discord", "channel")], client=object(),
    )
    assert [p.id for p in built] == ["a", "b"]
