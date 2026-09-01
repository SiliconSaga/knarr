"""Build publisher instances from config.

The mirror of build_adapter in src/watchers/run.py, and it fails the same
way: an unsupported pair raises with the publisher named, rather than
returning something that half works.
"""

from __future__ import annotations

from src.admin.config_schema import PublisherConfig
from src.publisher.adapters.discord import DiscordPublisher
from src.publisher.adapters.matrix import MatrixPublisher


class UnsupportedPublisher(Exception):
    """No adapter for this (platform, surface) pair."""


def build_publishers(configs: list[PublisherConfig], client) -> list:
    built = []
    for cfg in configs:
        pair = (cfg.platform, cfg.surface)
        if pair == ("matrix", "room"):
            built.append(MatrixPublisher(
                id=cfg.id, scope=cfg.scope, room_id=cfg.target, client=client,
            ))
        elif pair == ("discord", "channel"):
            built.append(DiscordPublisher(
                id=cfg.id, scope=cfg.scope, room_id=cfg.target, client=client,
            ))
        else:
            raise UnsupportedPublisher(
                f"publisher '{cfg.id}': no adapter for "
                f"{cfg.platform}/{cfg.surface}"
            )
    return built
