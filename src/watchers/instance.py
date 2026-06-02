"""WatcherInstance — generic runtime that hosts an Adapter and publishes alerts.

One instance per (platform, scope) config row. See
docs/plans/2026-05-30-knarr-source-identity-design.md § Pipeline
architecture for the model.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.admin.config_schema import InstanceConfig
from src.watchers.adapters import Adapter

if TYPE_CHECKING:
    from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class WatcherInstance:
    """Hosts an Adapter, persists the cursor, publishes alerts to Kafka.

    Stateless except for the dedup cursor, which lives in-memory for now
    (acceptable while the host pod is long-running; future phase moves
    this to Valkey for restart-resilience).
    """

    def __init__(
        self,
        config: InstanceConfig,
        adapter: Adapter,
        producer: Producer | Any,
        kafka_topic: str,
    ):
        self.config = config
        self.adapter = adapter
        self.producer = producer
        self.kafka_topic = kafka_topic
        self._cursor: str | None = None

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def interval_seconds(self) -> int:
        return int(self.config.polling.get("interval_seconds", 300))

    async def poll_once(self) -> int:
        """One poll cycle. Returns the number of alerts published."""
        alerts, new_cursor = await self.adapter.fetch(self._cursor)
        self._cursor = new_cursor

        for alert in alerts:
            self.producer.produce(
                self.kafka_topic,
                key=f"{alert.platform}:{alert.instance_id}",
                value=json.dumps(alert.to_kafka_dict()),
            )

        if alerts:
            self.producer.flush()
            logger.info(
                "instance=%s published=%d new_cursor=%s",
                self.config.id, len(alerts), new_cursor,
            )

        return len(alerts)
