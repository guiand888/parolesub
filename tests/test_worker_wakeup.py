"""Tests for the worker's jobs:new wake-up mechanism.

Replaces a plain `asyncio.sleep(1)` idle poll, which took a SQLite write
lock (claim_one's BEGIN IMMEDIATE) once a second forever, even with nothing
queued. Uses fakeredis's real pub/sub semantics (see
tests/test_queue_events.py) rather than mocking individual Redis calls,
since the coalescing/backoff logic in Worker._wait_for_wake depends on
genuine subscribe/publish/get_message behavior.
"""

import asyncio

import fakeredis.aioredis
import pytest

from audio_to_subs.api.settings import Settings
from audio_to_subs.queue_.events import publish_new
from audio_to_subs.worker.__main__ import Worker


def _make_worker(fallback_seconds: float) -> Worker:
    worker = Worker()
    worker._settings = Settings(
        SESSION_SECRET="test-secret-not-real",
        WORKER_POLL_FALLBACK_SECONDS=fallback_seconds,
    )
    return worker


async def _teardown(worker: Worker) -> None:
    if worker._pubsub is not None:
        await worker._pubsub.aclose()
    if worker._redis is not None:
        await worker._redis.aclose()


@pytest.mark.asyncio
async def test_publish_wakes_worker_well_before_the_fallback():
    worker = _make_worker(fallback_seconds=10.0)
    worker._redis = fakeredis.aioredis.FakeRedis()
    await worker._resubscribe()
    try:
        publisher = asyncio.create_task(
            _publish_after(worker._redis, "some-job-id", delay=0.05)
        )
        start = asyncio.get_event_loop().time()
        await asyncio.wait_for(worker._wait_for_wake(), timeout=2.0)
        elapsed = asyncio.get_event_loop().time() - start
        await publisher

        assert elapsed < 1.0  # far under the 10s fallback
    finally:
        await _teardown(worker)


@pytest.mark.asyncio
async def test_fallback_returns_on_its_own_with_no_notification():
    worker = _make_worker(fallback_seconds=0.1)
    worker._redis = fakeredis.aioredis.FakeRedis()
    await worker._resubscribe()
    try:
        # Must return once the (short) fallback interval elapses, with no
        # publish at all.
        await asyncio.wait_for(worker._wait_for_wake(), timeout=2.0)
    finally:
        await _teardown(worker)


@pytest.mark.asyncio
async def test_shutdown_interrupts_the_wait_immediately():
    worker = _make_worker(fallback_seconds=30.0)
    worker._redis = fakeredis.aioredis.FakeRedis()
    await worker._resubscribe()
    try:
        asyncio.create_task(_set_after(worker._shutdown_event, delay=0.05))
        start = asyncio.get_event_loop().time()
        await asyncio.wait_for(worker._wait_for_wake(), timeout=2.0)
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0  # far under the 30s fallback
    finally:
        await _teardown(worker)


@pytest.mark.asyncio
async def test_pubsub_failure_degrades_to_polling_then_recovers():
    worker = _make_worker(fallback_seconds=0.1)
    worker._redis = fakeredis.aioredis.FakeRedis()
    await worker._resubscribe()
    try:
        real_get_message = worker._pubsub.get_message
        state = {"raised": False}

        async def flaky_get_message(*args, **kwargs):
            if not state["raised"]:
                state["raised"] = True
                raise ConnectionError("simulated redis blip")
            return await real_get_message(*args, **kwargs)

        worker._pubsub.get_message = flaky_get_message
        broken_pubsub = worker._pubsub

        # First call: the pubsub read raises -> falls back to
        # asyncio.sleep(fallback) and resubscribes (fresh pubsub object).
        await asyncio.wait_for(worker._wait_for_wake(), timeout=2.0)
        assert state["raised"] is True
        assert worker._pubsub is not broken_pubsub

        # Recovered: a publish on the new subscription wakes it normally.
        publisher = asyncio.create_task(
            _publish_after(worker._redis, "another-job-id", delay=0.02)
        )
        await asyncio.wait_for(worker._wait_for_wake(), timeout=2.0)
        await publisher
    finally:
        await _teardown(worker)


async def _publish_after(redis, job_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    await publish_new(redis, job_id)


async def _set_after(event: asyncio.Event, delay: float) -> None:
    await asyncio.sleep(delay)
    event.set()
