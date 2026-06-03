"""Watcher runner — loads instance configs and polls each on its own cadence.

Replaces the hardcoded reddit+github wiring with a config-driven instance
list. See docs/plans/2026-05-30-knarr-source-identity-design.md.

Environment variables:
    KAFKA_BOOTSTRAP    Kafka bootstrap servers (required)
    KAFKA_TOPIC        Target topic (default: knarr.watch.alerts)
    KNARR_CONFIG_PATH  Path to the Knarr config YAML
                       (default: /etc/knarr/config.yaml)

Credentials are looked up from environment variables named in each
instance's `credentials_ref.secret_key`, which K8s mounts into the pod
via secretKeyRef. Adapter classes for the config's `platform` x
`access_path` are resolved by the dispatch table in this file.
"""

import asyncio
import logging
import os
import signal

from confluent_kafka import Producer

from src.admin.config_schema import InstanceConfig, load_config
from src.watchers.adapters.github_api import GitHubApiAdapter
from src.watchers.adapters.reddit_api import RedditApiAdapter
from src.watchers.instance import WatcherInstance

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class CredentialError(RuntimeError):
    """An instance declared credentials_ref but the secret is missing at runtime."""


def _resolve_credential(ref: dict | None, instance_id: str) -> str | None:
    """Read the secret value from the env var named by ref.secret_key.

    K8s mounts the secret via valueFrom.secretKeyRef into an env var. The
    env var name matches the secret's key field. Returns None if ref is
    None (instance requires no auth).

    Fails fast when a credentials_ref IS configured but the env var is
    unset or empty — silently degrading to anonymous calls masks real
    secret-wiring mistakes (e.g., the secretKeyRef points at a Secret
    that doesn't exist, or the Secret key was renamed).
    """
    if ref is None:
        return None
    env_var = ref.get("secret_key")
    if not env_var:
        raise CredentialError(
            f"instance={instance_id}: credentials_ref configured but "
            f"secret_key missing from the ref dict ({ref!r})"
        )
    value = os.environ.get(env_var)
    if not value:
        raise CredentialError(
            f"instance={instance_id}: credentials_ref names env var "
            f"{env_var!r} but it is unset or empty. Check the pod's "
            f"secretKeyRef wiring against Secret "
            f"{ref.get('secret_name')!r}."
        )
    return value


def build_adapter(config: InstanceConfig):
    """Dispatch (platform, access_path) -> concrete Adapter constructor."""
    p = config.platform
    a = config.access_path
    if p == "reddit" and a == "api":
        return RedditApiAdapter(
            instance_id=config.id,
            scope=config.scope,
            subreddit=config.platform_config["subreddit"],
        )
    if p == "github" and a == "api":
        return GitHubApiAdapter(
            instance_id=config.id,
            scope=config.scope,
            repos=config.platform_config["repos"],
            token=_resolve_credential(config.credentials_ref, config.id),
        )
    raise ValueError(
        f"No adapter registered for platform={p!r} access_path={a!r} "
        f"(instance={config.id!r}). Phase 1 supports reddit/api and github/api."
    )


def build_instances(config_path: str, producer: Producer,
                    kafka_topic: str) -> list[WatcherInstance]:
    """Load config, build adapters, wrap in WatcherInstances."""
    knarr_config = load_config(config_path)
    instances: list[WatcherInstance] = []
    for inst_cfg in knarr_config.instances:
        adapter = build_adapter(inst_cfg)
        instances.append(WatcherInstance(
            config=inst_cfg,
            adapter=adapter,
            producer=producer,
            kafka_topic=kafka_topic,
        ))
    return instances


async def _poll_loop(instance: WatcherInstance, stop_event: asyncio.Event):
    """Per-instance poll loop. Sleeps interval between cycles; bails on stop."""
    while not stop_event.is_set():
        try:
            await instance.poll_once()
        except Exception:
            logger.exception("instance=%s poll cycle failed", instance.id)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=instance.interval_seconds,
            )
        except TimeoutError:
            continue  # interval elapsed; loop


async def main():
    kafka_bootstrap = os.environ["KAFKA_BOOTSTRAP"]
    kafka_topic = os.environ.get("KAFKA_TOPIC", "knarr.watch.alerts")
    config_path = os.environ.get("KNARR_CONFIG_PATH", "/etc/knarr/config.yaml")

    producer = Producer({"bootstrap.servers": kafka_bootstrap})
    instances = build_instances(config_path, producer, kafka_topic)

    if not instances:
        logger.warning("no instances configured at %s; nothing to poll", config_path)
        return

    logger.info(
        "watcher started — %d instance(s): %s",
        len(instances),
        ", ".join(i.id for i in instances),
    )

    stop_event = asyncio.Event()

    def handle_signal(signum, frame):
        logger.info("signal %s received; shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    await asyncio.gather(
        *[_poll_loop(inst, stop_event) for inst in instances]
    )


if __name__ == "__main__":
    asyncio.run(main())
