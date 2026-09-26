import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from cloud.store import Conflict, Store


def submit(store, owner, **kwargs):
    return store.submit(owner, "test-" + owner, {"request": "hello"}, "conversation", **kwargs)[0]


def claim(store, owner, **kwargs):
    return store.claim("worker-" + uuid.uuid4().hex, ["test-" + owner], 90, **kwargs)


def test_result_and_history_survive_new_connection(store, owner):
    job = submit(store, owner)
    claimed = claim(store, owner)
    assert store.finish(claimed, result={"output": "durable"})
    reopened = Store(str(store.engine.url.render_as_string(hide_password=False)))
    assert reopened.get(job["id"], owner)["result"] == {"output": "durable"}
    assert [e["status"] for e in reopened.timeline(job["id"], owner)] == ["queued", "running", "succeeded"]
    reopened.engine.dispose()


def test_idempotent_submission_and_conflicting_payload(store, owner):
    first = submit(store, owner, key="one")
    replay = submit(store, owner, key="one")
    assert first["id"] == replay["id"]
    with pytest.raises(Conflict):
        store.submit(owner, "test-" + owner, {"request": "different"}, "conversation", "one")
    assert len(store.history(owner)) == 1


def test_concurrent_submission_creates_one_job(store, owner):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: submit(store, owner, key="concurrent")["id"], range(16)))
    assert len(set(ids)) == 1


def test_concurrent_workers_claim_once(store, owner):
    expected = {submit(store, owner)["id"] for _ in range(12)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: claim(store, owner), range(20)))
    actual = [job["id"] for job in claims if job]
    assert len(actual) == len(set(actual)) == 12
    assert set(actual) == expected


def test_crash_recovery_fences_late_worker(store, owner):
    job = submit(store, owner)
    now = time.time() + 1
    old = claim(store, owner, now=now)
    replacement = claim(store, owner, now=now + 91)
    assert replacement["id"] == old["id"] == job["id"]
    assert replacement["attempts"] == 2
    assert not store.finish(old, result={"output": "stale"}, now=now + 92)
    assert store.finish(replacement, result={"output": "recovered"}, now=now + 92)


def test_expired_worker_cannot_finish_even_before_reclaim(store, owner):
    submit(store, owner)
    now = time.time() + 1
    job = claim(store, owner, now=now)
    assert not store.finish(job, result={}, now=now + 91)


def test_crashed_final_attempt_becomes_dead(store, owner):
    job = submit(store, owner, max_attempts=1)
    now = time.time() + 1
    claim(store, owner, now=now)
    assert claim(store, owner, now=now + 91) is None
    assert store.get(job["id"], owner)["status"] == "dead"


def test_retry_backoff_and_exhaustion(store, owner):
    original = submit(store, owner, max_attempts=2)
    now = time.time() + 1
    first = claim(store, owner, now=now)
    store.finish(first, error="Transient failure", now=now + 1)
    assert claim(store, owner, now=now + 2) is None
    second = claim(store, owner, now=now + 4)
    assert second["attempts"] == 2
    store.finish(second, error="Still failing", now=now + 5)
    assert store.get(original["id"], owner)["status"] == "dead"


def test_cancel_fences_running_work(store, owner):
    original = submit(store, owner)
    running = claim(store, owner)
    assert store.cancel(original["id"], owner)
    assert not store.finish(running, result={"output": "too late"})
    assert not store.cancel(original["id"], owner)
    assert store.get(original["id"], owner)["status"] == "cancelled"


def test_owner_isolation_and_plugin_routing(store, owner):
    job = submit(store, owner)
    assert store.get(job["id"], "other") is None
    assert store.timeline(job["id"], "other") is None
    assert not store.cancel(job["id"], "other")
    assert store.history("unknown-" + owner) == []
    assert store.claim("wrong-worker", ["unsupported-" + owner], 90) is None
