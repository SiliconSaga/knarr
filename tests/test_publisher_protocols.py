from src.publisher.protocols import Constraints, capabilities_of
from src.publisher.schemas import PublishResult


class _BroadcastOnly:
    id = "b"
    scope = "community/x"
    platform = "fake"
    surface = "feed"
    constraints = Constraints()

    async def post(self, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []


class _ConversationalOnly:
    id = "c"
    scope = "community/x"
    platform = "fake"
    surface = "dm"
    constraints = Constraints()

    async def message(self, thread, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []


def test_capabilities_are_derived_from_implemented_methods():
    """Derived, never configured — a YAML list would drift from the code."""
    assert capabilities_of(_BroadcastOnly()) == frozenset({"post"})
    assert capabilities_of(_ConversationalOnly()) == frozenset({"message"})


def test_a_publisher_does_not_advertise_what_it_cannot_do():
    assert "message" not in capabilities_of(_BroadcastOnly())
    assert "post" not in capabilities_of(_ConversationalOnly())


def test_constraints_default_to_permissive():
    c = Constraints()
    assert c.media_required is False
    assert c.media_formats == ()
    assert c.max_length is None
