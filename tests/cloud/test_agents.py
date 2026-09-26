import asyncio
from dataclasses import replace

import httpx
import pytest

from cloud.agents import PLUGINS
from cloud.config import Settings
from cloud.worker import execute_job
from shared.gemini_http import cloud_enabled, generate_text


@pytest.mark.asyncio
async def test_existing_agents_in_mock_mode(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-used")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Mock mode must not make network requests")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    result = await PLUGINS["gemini-wrapper"].execute({"request": "hello"})
    assert result["mock"] is True
    result = await PLUGINS["assignment-coach"].execute({"assignment_title": "Queue design", "subject": "Cloud"})
    assert len(result["output"]["response"]["task_plan"]) == 4


@pytest.mark.asyncio
async def test_gemini_cloud_protocol(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "cloud")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async def respond(self, url, **kwargs):
        assert url.endswith(":generateContent")
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        assert kwargs["json"]["contents"][0]["parts"][0]["text"] == "hello"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "answer"}]}}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    assert cloud_enabled()
    assert await generate_text("hello") == "answer"


@pytest.mark.asyncio
async def test_provider_failure_is_not_silently_mocked(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "cloud")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    calls = []

    async def fail(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(503, json={"error": "provider unavailable"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fail)
    with pytest.raises(RuntimeError):
        await PLUGINS["gemini-wrapper"].execute({"request": "hello"})
    with pytest.raises(RuntimeError):
        await PLUGINS["assignment-coach"].execute({"assignment_title": "Queue design"})
    assert len(calls) == 2  # Each failed agent call stops at its first provider error.


@pytest.mark.asyncio
async def test_worker_persists_result(store, owner, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "mock")
    job, _ = store.submit(owner, "gemini-wrapper", {"request": "hello"}, "default")
    # Restrict the claim by a unique plugin name to isolate shared PostgreSQL tests.
    name = "worker-test-" + owner
    from sqlalchemy import update
    from cloud.store import jobs
    with store.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(agent=name))
    monkeypatch.setitem(PLUGINS, name, PLUGINS["gemini-wrapper"])
    claimed = store.claim("test-worker", [name], 90)
    await execute_job(store, Settings(), claimed)
    assert store.get(job["id"], owner)["status"] == "succeeded"


@pytest.mark.asyncio
async def test_worker_timeout_is_retryable(store, owner, monkeypatch):
    async def slow(payload):
        await asyncio.sleep(10)
    name = "slow-" + owner
    monkeypatch.setitem(PLUGINS, name, replace(PLUGINS["gemini-wrapper"], execute=slow))
    job, _ = store.submit(owner, name, {}, "default")
    claimed = store.claim("test-worker", [name], 90)
    await execute_job(store, Settings(task_timeout=0.01), claimed)
    assert store.get(job["id"], owner)["status"] == "retry"
    assert store.get(job["id"], owner)["error"] == "Task timed out"
