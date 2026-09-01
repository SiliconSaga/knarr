from datetime import UTC, datetime

import pytest

from src.watchers.adapters import Adapter
from src.watchers.schemas import Content, WatchAlert


class _FakeAdapter:
    """Minimal Protocol-satisfying adapter for testing."""

    def __init__(self):
        self._cursor: str | None = None

    async def fetch(self, since_cursor: str | None) -> tuple[list[WatchAlert], str | None]:
        if since_cursor is None:
            alerts = [
                WatchAlert(
                    event_id=f"fake_{i}",
                    instance_id="fake-instance",
                    scope="community/test",
                    access_path="api",
                    platform="fake",
                    raw_post_ref=f"https://fake/{i}",
                    content=Content(
                        type="post", title=f"Post {i}", body="body",
                        author="someone", attachments=[],
                    ),
                    timestamp=datetime.now(UTC).isoformat(),
                )
                for i in range(3)
            ]
            return alerts, "fake_2"
        return [], since_cursor


def test_adapter_protocol_accepts_fake():
    """Anything with the right shape satisfies the Protocol."""
    adapter: Adapter = _FakeAdapter()
    assert hasattr(adapter, "fetch")


@pytest.mark.asyncio
async def test_fake_adapter_returns_alerts_and_cursor():
    adapter = _FakeAdapter()
    alerts, cursor = await adapter.fetch(None)
    assert len(alerts) == 3
    assert cursor == "fake_2"
    alerts2, cursor2 = await adapter.fetch(cursor)
    assert alerts2 == []
    assert cursor2 == cursor
