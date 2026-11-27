import pytest

from src.publisher.protocols import Constraints
from src.publisher.registry import AuthorizationError, PublisherRegistry
from src.publisher.schemas import ObjectRef, PublishResult, Target
from src.watchers.schemas import Content


class _FakePublisher:
    def __init__(self, pid, scope, verbs=("post",)):
        self.id = pid
        self.scope = scope
        self.platform = "fake"
        self.surface = "feed"
        self.constraints = Constraints()
        self.credentials_resolved = False
        if "post" in verbs:
            self.post = self._post
        if "message" in verbs:
            self.message = self._message

    async def _post(self, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    async def _message(self, thread, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []

    def resolve_credentials(self):
        self.credentials_resolved = True


def _target(pid="p1", verb="post", parent=None):
    return Target(
        publisher_id=pid, verb=verb, parent=parent,
        content=Content(type="post", title="", body="b", author="a", attachments=[]),
    )


def _ref(pid="p1", platform="fake", surface="feed"):
    return ObjectRef(publisher_id=pid, platform=platform, surface=surface,
                     object_type="post", object_id="$e")


def test_accepts_a_parent_from_the_same_publisher():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl", verbs=("message",))])
    assert reg.resolve(_target(verb="message", parent=_ref()), "community/mtl")


@pytest.mark.parametrize("parent", [
    _ref(pid="p2"), _ref(platform="matrix"), _ref(surface="room"),
])
def test_rejects_a_parent_from_another_destination(parent):
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl", verbs=("message",))])
    with pytest.raises(AuthorizationError, match="cannot reply"):
        reg.resolve(_target(verb="message", parent=parent), "community/mtl")


def test_authorize_all_rejects_a_duplicate_publisher_and_verb():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError, match="twice"):
        reg.authorize_all([_target("p1"), _target("p1")], "community/mtl")


def test_resolves_a_known_publisher():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    assert reg.resolve(_target(), "community/mtl").id == "p1"


def test_rejects_unknown_publisher_id():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError, match="unknown publisher"):
        reg.resolve(_target("nope"), "community/mtl")


def test_rejects_cross_scope_target():
    """The worst failure this system could have: one community's content
    published through another's credentials."""
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError, match="scope"):
        reg.resolve(_target(), "group/pta")


def test_rejects_a_verb_the_publisher_cannot_do():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl", verbs=("post",))])
    with pytest.raises(AuthorizationError, match="cannot message"):
        reg.resolve(_target(verb="message"), "community/mtl")


def test_credentials_are_not_resolved_before_authorization_fails():
    """Ordering is a security property: a rejected target must never have
    reached a secret."""
    pub = _FakePublisher("p1", "community/mtl")
    reg = PublisherRegistry([pub])
    with pytest.raises(AuthorizationError):
        reg.resolve(_target(), "group/pta")
    assert pub.credentials_resolved is False


def test_authorize_all_rejects_the_whole_request_on_one_bad_target():
    """A request naming a destination it has no business touching is not
    partially trustworthy."""
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError):
        reg.authorize_all([_target("p1"), _target("nope")], "community/mtl")
