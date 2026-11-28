import json

import pytest

from src.publisher.fanout import fan_out
from src.publisher.protocols import Constraints
from src.publisher.registry import PublisherRegistry
from src.publisher.schemas import (
    ObjectRef,
    PublishRequest,
    PublishResult,
    Target,
    Violation,
)
from src.publisher.store import JsonResultStore
from src.watchers.schemas import Content


class _Pub:
    def __init__(self, pid, scope="community/mtl", fail=False, violations=()):
        self.id = pid
        self.scope = scope
        self.platform = "fake"
        self.surface = "feed"
        self.constraints = Constraints()
        self.calls = 0
        self._fail = fail
        self._violations = list(violations)

    async def post(self, content, media=None):
        self.calls += 1
        if self._fail:
            return PublishResult(ok=False, ref=None, error="boom")
        return PublishResult(
            ok=True,
            ref=ObjectRef(publisher_id=self.id, platform="fake", surface="feed",
                          object_type="post", object_id=f"${self.id}"),
            error=None,
        )

    def validate(self, verb, content, media=None):
        return list(self._violations)


def _request(targets, rid="r1"):
    return PublishRequest(
        request_id=rid, scope="community/mtl", targets=targets,
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
    )


def _target(pid):
    return Target(
        publisher_id=pid, verb="post",
        content=Content(type="post", title="", body="b", author="a", attachments=[]),
    )


class _Commenter(_Pub):
    async def comment(self, parent, content, media=None):
        self.calls += 1
        return PublishResult(ok=True, ref=None, error=None)


@pytest.mark.asyncio
async def test_parentless_comment_is_blocked_not_raised(tmp_path):
    """A reply with nothing to reply to is a per-target violation; the other
    targets still go out."""
    c, p = _Commenter("c"), _Pub("p")
    orphan = Target(
        publisher_id="c", verb="comment",
        content=Content(type="post", title="", body="b", author="a", attachments=[]),
    )
    report = await fan_out(
        _request([orphan, _target("p")]), PublisherRegistry([c, p]),
        JsonResultStore(tmp_path / "s.json"),
    )
    assert c.calls == 0
    assert p.calls == 1
    assert [v.publisher_id for v in report.violations] == ["c"]


@pytest.mark.asyncio
async def test_publishes_every_target(tmp_path):
    a, b = _Pub("a"), _Pub("b")
    report = await fan_out(
        _request([_target("a"), _target("b")]),
        PublisherRegistry([a, b]),
        JsonResultStore(tmp_path / "s.json"),
    )
    assert report.published == 2
    assert a.calls == 1 and b.calls == 1


@pytest.mark.asyncio
async def test_rerun_reposts_nothing(tmp_path):
    """The Phase A exit criterion. A retry must skip what already landed."""
    a, b = _Pub("a"), _Pub("b")
    store = JsonResultStore(tmp_path / "s.json")
    req = _request([_target("a"), _target("b")])

    await fan_out(req, PublisherRegistry([a, b]), store)
    second = await fan_out(req, PublisherRegistry([a, b]), store)

    assert second.published == 0
    assert second.skipped == 2
    assert a.calls == 1 and b.calls == 1


@pytest.mark.asyncio
async def test_partial_failure_retries_only_the_failed_target(tmp_path):
    a, b = _Pub("a"), _Pub("b", fail=True)
    store = JsonResultStore(tmp_path / "s.json")
    req = _request([_target("a"), _target("b")])

    first = await fan_out(req, PublisherRegistry([a, b]), store)
    assert first.published == 1 and first.failed == 1

    b._fail = False
    second = await fan_out(req, PublisherRegistry([a, b]), store)
    assert second.published == 1 and second.skipped == 1
    assert a.calls == 1, "the target that already succeeded must not be retried"
    assert b.calls == 2


@pytest.mark.asyncio
async def test_dry_run_publishes_nothing_and_records_nothing(tmp_path):
    a = _Pub("a")
    store = JsonResultStore(tmp_path / "s.json")
    report = await fan_out(_request([_target("a")]), PublisherRegistry([a]),
                           store, dry_run=True)
    assert a.calls == 0
    assert report.published == 0
    assert store.succeeded("r1", "a", "post") is False


@pytest.mark.asyncio
async def test_validation_failure_blocks_that_target(tmp_path):
    bad = _Pub("a", violations=[Violation(publisher_id="a", reason="media required")])
    report = await fan_out(_request([_target("a")]), PublisherRegistry([bad]),
                           JsonResultStore(tmp_path / "s.json"))
    assert bad.calls == 0
    assert len(report.violations) == 1


@pytest.mark.asyncio
async def test_source_is_recorded_with_each_result(tmp_path):
    a = _Pub("a")
    path = tmp_path / "s.json"
    req = _request([_target("a")])
    req.source = "borgr/hit-list"
    await fan_out(req, PublisherRegistry([a]), JsonResultStore(path))
    assert json.loads(path.read_text())["r1|a|post"]["source"] == "borgr/hit-list"


@pytest.mark.asyncio
async def test_dry_run_reports_violations(tmp_path):
    """A dry run's whole value is finding these before the platform does."""
    bad = _Pub("a", violations=[Violation(publisher_id="a", reason="JPEG only")])
    report = await fan_out(_request([_target("a")]), PublisherRegistry([bad]),
                           JsonResultStore(tmp_path / "s.json"), dry_run=True)
    assert "JPEG only" in report.violations[0].reason
