import pytest

from src.publisher.schemas import (
    MediaRef,
    ObjectRef,
    PublishRequest,
    PublishResult,
    Target,
    Violation,
    derive_request_id,
)
from src.watchers.schemas import Content


def _content(body="hello"):
    return Content(type="post", title="T", body=body, author="knarr", attachments=[])


def _target(publisher_id="mtl-matrix", verb="post", body="hello"):
    return Target(publisher_id=publisher_id, verb=verb, content=_content(body), parent=None)


def test_object_ref_is_composite():
    """A bare platform id cannot select an adapter or its credentials later."""
    ref = ObjectRef(
        publisher_id="mtl-matrix", platform="matrix", surface="room",
        object_type="post", object_id="$abc",
    )
    assert ref.publisher_id == "mtl-matrix"
    assert ref.object_id == "$abc"


def test_request_id_is_stable_for_identical_input():
    a = derive_request_id("community/mtl", [_target()], "digest")
    b = derive_request_id("community/mtl", [_target()], "digest")
    assert a == b


def test_request_id_changes_with_content():
    a = derive_request_id("community/mtl", [_target(body="one")], "d1")
    b = derive_request_id("community/mtl", [_target(body="two")], "d2")
    assert a != b


def test_request_id_ignores_target_order():
    """Target order is not meaningful, so it must not change identity.

    Otherwise a retry that resolved targets in a different order would look
    like a different request and republish everything.
    """
    t1, t2 = _target("a"), _target("b")
    assert derive_request_id("s", [t1, t2], "d") == derive_request_id("s", [t2, t1], "d")


def test_request_id_changes_with_scope():
    assert derive_request_id("community/mtl", [_target()], "d") != \
        derive_request_id("group/pta", [_target()], "d")


def test_request_id_changes_with_parent():
    """A reply to one post is not a retry of a reply to another."""
    def reply(object_id):
        parent = None if object_id is None else ObjectRef(
            publisher_id="p", platform="matrix", surface="room",
            object_type="post", object_id=object_id,
        )
        return Target(publisher_id="p", verb="comment", content=_content(),
                      parent=parent)

    ids = {derive_request_id("s", [reply(oid)], "d") for oid in (None, "$a", "$b")}
    assert len(ids) == 3


def test_violation_carries_the_target_it_belongs_to():
    v = Violation(publisher_id="mtl-instagram", reason="media required")
    assert "media" in v.reason


def test_media_ref_round_trips():
    m = MediaRef(kind="public-url", locator="https://x/y.jpg",
                 content_type="image/jpeg", checksum="abc")
    assert m.kind == "public-url"


def test_publish_result_records_the_object_ref():
    ref = ObjectRef(publisher_id="p", platform="matrix", surface="room",
                    object_type="post", object_id="$1")
    assert PublishResult(ok=True, ref=ref, error=None).ref.object_id == "$1"


def test_publish_request_holds_targets():
    req = PublishRequest(
        request_id="r1", scope="community/mtl", targets=[_target()],
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
    )
    assert req.targets[0].publisher_id == "mtl-matrix"


def _request(source):
    return PublishRequest(
        request_id="r1", scope="user/cervator", targets=[_target()],
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
        source=source,
    )


def test_source_defaults_to_none():
    req = PublishRequest(
        request_id="r1", scope="community/mtl", targets=[_target()],
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
    )
    assert req.source is None


@pytest.mark.parametrize("source", ["borgr/hit-list", "borgr/run-failed", "cron", "a.b/c_d"])
def test_well_formed_sources_are_accepted(source):
    assert _request(source).source == source


@pytest.mark.parametrize("source", [
    "", "Borgr/hit-list", "borgr hit-list", "borgr//x", "/borgr", "borgr/",
    "borgr/\nx", "x" * 65,
])
def test_malformed_sources_are_rejected(source):
    with pytest.raises(ValueError, match="source"):
        _request(source)
