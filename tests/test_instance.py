import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.admin.config_schema import InstanceConfig
from src.watchers.instance import WatcherInstance
from src.watchers.schemas import Content, WatchAlert


def _alert(event_id: str) -> WatchAlert:
    return WatchAlert(
        event_id=event_id,
        instance_id="test-instance",
        scope="community/test",
        access_path="api",
        platform="fake",
        raw_post_ref=f"https://fake/{event_id}",
        content=Content(
            type="post", title="T", body="B", author="a", attachments=[],
        ),
        timestamp=datetime.now(UTC).isoformat(),
    )


class _StubAdapter:
    """Returns scripted (alerts, cursor) sequences."""

    def __init__(self, scripted: list[tuple[list[WatchAlert], str | None]]):
        self._scripted = list(scripted)
        self.calls: list[str | None] = []

    async def fetch(self, since_cursor):
        self.calls.append(since_cursor)
        if self._scripted:
            return self._scripted.pop(0)
        return [], since_cursor


def _config() -> InstanceConfig:
    return InstanceConfig(
        id="test-instance",
        platform="fake",
        access_path="api",
        scope="community/test",
        polling={"interval_seconds": 60},
        platform_config={},
        target_room="some-room",
    )


@pytest.mark.asyncio
async def test_instance_passes_no_cursor_on_first_poll():
    adapter = _StubAdapter([([_alert("a")], "cursor1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    assert adapter.calls == [None]


@pytest.mark.asyncio
async def test_instance_stores_cursor_between_polls():
    adapter = _StubAdapter([
        ([_alert("a")], "cursor1"),
        ([_alert("b")], "cursor2"),
    ])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    await inst.poll_once()
    assert adapter.calls == [None, "cursor1"]


@pytest.mark.asyncio
async def test_instance_publishes_each_alert_to_kafka():
    adapter = _StubAdapter([([_alert("a"), _alert("b")], "c1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()

    assert producer.produce.call_count == 2
    first_call = producer.produce.call_args_list[0]
    assert first_call.args[0] == "knarr.watch.alerts"
    payload = json.loads(first_call.kwargs["value"])
    assert payload["event_id"] == "a"
    assert payload["instance_id"] == "test-instance"
    producer.flush.assert_called_once()


@pytest.mark.asyncio
async def test_instance_skips_publish_when_no_alerts():
    adapter = _StubAdapter([([], "c1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    producer.produce.assert_not_called()
    producer.flush.assert_not_called()
