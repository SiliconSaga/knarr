"""Publish one request to many destinations, safely on a retry.

Fan-out is partial by nature: N destinations is N independent calls, any of
which can fail. So each target's outcome is recorded before the next is
attempted, and a retry skips what already succeeded. Without that, the second
run of a half-failed publish sends the community the same announcement twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.publisher.registry import PublisherRegistry
from src.publisher.schemas import PublishRequest, Violation
from src.publisher.store import ResultStore

logger = logging.getLogger(__name__)


@dataclass
class FanOutReport:
    published: int = 0
    skipped: int = 0
    failed: int = 0
    violations: list[Violation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def fan_out(
    request: PublishRequest,
    registry: PublisherRegistry,
    store: ResultStore,
    dry_run: bool = False,
) -> FanOutReport:
    """Authorize, validate, then publish each target independently."""
    report = FanOutReport()

    # Whole-request authorization first. A request naming a destination it has
    # no business touching is not partially trustworthy, so nothing is
    # attempted until every target checks out.
    publishers = registry.authorize_all(request.targets, request.scope)

    for target, publisher in zip(request.targets, publishers, strict=True):
        if store.succeeded(request.request_id, publisher.id, target.verb):
            logger.info(
                "skip %s/%s — already published for request %s",
                publisher.id, target.verb, request.request_id,
            )
            report.skipped += 1
            continue

        violations = publisher.validate(target.verb, target.content, target.media)
        if target.verb != "post" and target.parent is None:
            violations.append(Violation(
                publisher_id=publisher.id,
                reason=f"{target.verb} needs a parent to reply to",
            ))
        if violations:
            report.violations.extend(violations)
            logger.warning(
                "%s/%s blocked by %d validation failure(s)",
                publisher.id, target.verb, len(violations),
            )
            continue

        if dry_run:
            logger.info(
                "DRY RUN would %s to %s: %r",
                target.verb, publisher.id, target.content.body[:80],
            )
            continue

        method = getattr(publisher, target.verb)
        args = ((target.content,) if target.verb == "post"
                else (target.parent, target.content))
        result = await method(*args, media=target.media)

        if not result.ok or result.ref is None:
            report.failed += 1
            report.errors.append(f"{publisher.id}/{target.verb}: {result.error}")
            logger.error(
                "%s/%s FAILED: %s", publisher.id, target.verb, result.error,
            )
            continue

        # Recorded before the next target is attempted, so an interruption
        # mid-fan-out still leaves an accurate account of what landed.
        store.record(request.request_id, publisher.id, target.verb, result.ref,
                     source=request.source)
        report.published += 1
        logger.info(
            "%s/%s published as %s",
            publisher.id, target.verb, result.ref.object_id,
        )

    return report
