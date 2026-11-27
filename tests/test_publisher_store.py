import pytest

from src.publisher.schemas import ObjectRef
from src.publisher.store import JsonResultStore, StoreError


def _ref(object_id="$1"):
    return ObjectRef(publisher_id="p1", platform="matrix", surface="room",
                     object_type="post", object_id=object_id)


def test_records_and_reads_back(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref())
    assert store.get("r1", "p1", "post").object_id == "$1"


def test_unknown_key_returns_none(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    assert store.get("r1", "p1", "post") is None
    assert store.succeeded("r1", "p1", "post") is False


def test_survives_a_new_process(tmp_path):
    """Persistence is the whole point — an in-memory store would make a
    retry after a crash republish everything."""
    path = tmp_path / "results.json"
    JsonResultStore(path).record("r1", "p1", "post", _ref("$abc"))
    assert JsonResultStore(path).get("r1", "p1", "post").object_id == "$abc"


def test_keys_are_composite(tmp_path):
    """Same request, different target — must not collide."""
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref("$one"))
    store.record("r1", "p2", "post", _ref("$two"))
    assert store.get("r1", "p1", "post").object_id == "$one"
    assert store.get("r1", "p2", "post").object_id == "$two"


def test_same_publisher_different_verb_does_not_collide(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref("$post"))
    store.record("r1", "p1", "comment", _ref("$comment"))
    assert store.get("r1", "p1", "post").object_id == "$post"
    assert store.get("r1", "p1", "comment").object_id == "$comment"


def test_corrupt_store_file_does_not_crash(tmp_path):
    """A store that cannot be read must not take the publish down — but it
    must also not silently report 'nothing published', which would repost."""
    path = tmp_path / "results.json"
    path.write_text("{ not json")
    store = JsonResultStore(path)
    try:
        store.get("r1", "p1", "post")
    except Exception as exc:  # noqa: BLE001
        assert "corrupt" in str(exc).lower()
    else:
        raise AssertionError("a corrupt store must raise, not report empty")


def test_non_object_root_is_corrupt(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("[]")
    with pytest.raises(StoreError):
        JsonResultStore(path).succeeded("r1", "p1", "post")


def test_malformed_entry_raises_rather_than_reading_as_absent(tmp_path):
    """A null or empty entry is corruption. Reading it as 'never sent' would
    make the fan-out republish."""
    path = tmp_path / "results.json"
    path.write_text('{"r1|p1|post": null, "r1|p2|post": {}}')
    store = JsonResultStore(path)
    for pid in ("p1", "p2"):
        with pytest.raises(StoreError):
            store.succeeded("r1", pid, "post")
