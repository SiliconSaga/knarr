"""Resolve publisher ids to instances, and authorize before anything else.

The ordering here is the point. A PublishRequest names targets by id; nothing
in the id says whose behalf it acts on. Checking the RESOLVED instance's scope
against the request's — before a credential is touched — is what stops a typo
or a compromised manifest publishing one community's content through
another's Page.
"""

from __future__ import annotations

from src.publisher.protocols import capabilities_of
from src.publisher.schemas import Target


class AuthorizationError(Exception):
    """A target is unknown, out of scope, or asks for a verb it cannot do."""


class PublisherRegistry:
    def __init__(self, publishers: list):
        self._by_id = {p.id: p for p in publishers}

    def resolve(self, target: Target, request_scope: str):
        """Resolve and authorize one target. Raises rather than returning None."""
        publisher = self._by_id.get(target.publisher_id)
        if publisher is None:
            raise AuthorizationError(
                f"unknown publisher '{target.publisher_id}' "
                f"(known: {sorted(self._by_id)})"
            )

        # Scope is taken from the RESOLVED instance, never from the request.
        if publisher.scope != request_scope:
            raise AuthorizationError(
                f"scope mismatch: request is '{request_scope}' but publisher "
                f"'{publisher.id}' serves '{publisher.scope}'. Refusing — this "
                f"is a bug or an attack, never a routine condition."
            )

        if target.verb not in capabilities_of(publisher):
            raise AuthorizationError(
                f"'{publisher.id}' cannot {target.verb}; it can "
                f"{sorted(capabilities_of(publisher)) or 'nothing'}"
            )

        return publisher

    def authorize_all(self, targets: list[Target], request_scope: str) -> list:
        """Authorize every target, or reject the whole request.

        Whole-request rather than per-target on purpose: a request naming a
        destination it has no business touching is not partially trustworthy.
        """
        return [self.resolve(t, request_scope) for t in targets]
