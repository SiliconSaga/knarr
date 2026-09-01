"""WatcherInstance — generic runtime that hosts an Adapter and publishes alerts.

One instance per (platform, scope) config row. See
docs/plans/2026-05-30-knarr-source-identity-design.md § Pipeline
architecture for the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from src.admin.config_schema import InstanceConfig
from src.watchers.adapters import Adapter

if TYPE_CHECKING:
    from confluent_kafka import Producer

logger = logging.getLogger(__name__)

# How long librdkafka is given to drain the queue. Finite so a wedged broker
# surfaces as "cursor held, will retry" rather than an instance that never
# returns from a poll.
_FLUSH_TIMEOUT_SECONDS = 30


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
        """One poll cycle. Returns the number of alerts published.

        The cursor advances ONLY after every alert in the batch is
        confirmed delivered. Advancing it first — as this did originally —
        means a producer failure permanently skips the alerts that failed:
        the next poll asks the platform for everything *after* a cursor
        covering events that never reached Kafka, and nothing ever
        notices. Losing alerts silently is worse than re-delivering a few.
        """
        alerts, new_cursor = await self.adapter.fetch(self._cursor)
        if not alerts:
            # Nothing to deliver, so nothing to lose. Still take the cursor:
            # an empty fetch legitimately moves the tip forward.
            self._cursor = new_cursor
            return 0

        failures: list[str] = []

        def _on_delivery(err, msg):
            # Called by confluent_kafka during flush(). Errors arrive HERE,
            # not from produce(), so a batch can "send" cleanly and still
            # have failed — which is the whole reason for this callback.
            if err is not None:
                failures.append(str(err))

        try:
            for alert in alerts:
                self.producer.produce(
                    self.kafka_topic,
                    key=f"{alert.platform}:{alert.instance_id}",
                    value=json.dumps(alert.to_kafka_dict()),
                    on_delivery=_on_delivery,
                )
        except BufferError as exc:
            # Local queue full. Whatever was already queued still flushes
            # below, but the batch is incomplete, so the cursor must not move.
            logger.error(
                "instance=%s produce buffer full (%s); keeping cursor at %s",
                self.config.id, exc, self._cursor,
            )
            self.producer.flush()
            return 0

        # flush() is a blocking librdkafka call. Awaiting it on a worker
        # thread matters because every instance shares one event loop: a
        # broker that has gone slow would otherwise stall every OTHER
        # instance's poll for the duration, turning one degraded platform
        # into a stalled watcher. The timeout bounds that further — a flush
        # that never returns must not park the loop forever.
        try:
            remaining = await asyncio.wait_for(
                asyncio.to_thread(self.producer.flush, _FLUSH_TIMEOUT_SECONDS),
                timeout=_FLUSH_TIMEOUT_SECONDS + 5,
            )
        except TimeoutError:
            logger.error(
                "instance=%s flush did not return within %ss; keeping cursor "
                "at %s so the next poll retries",
                self.config.id, _FLUSH_TIMEOUT_SECONDS + 5, self._cursor,
            )
            return 0

        if remaining or failures:
            logger.error(
                "instance=%s delivery incomplete (undelivered=%s failures=%s); "
                "keeping cursor at %s so the next poll retries",
                self.config.id, remaining, failures or "none", self._cursor,
            )
            return 0

        self._cursor = new_cursor
        logger.info(
            "instance=%s published=%d new_cursor=%s",
            self.config.id, len(alerts), new_cursor,
        )
        return len(alerts)
