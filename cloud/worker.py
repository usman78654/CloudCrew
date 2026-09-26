"""One job per process; scale the process/container count for concurrency."""
import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from pathlib import Path

from cloud.agents import PLUGINS
from cloud.config import Settings
from cloud.store import Store

log = logging.getLogger("cloud.worker")


async def execute_job(store, settings, job):
    try:
        plugin = PLUGINS[job["agent"]]

        async def progress(detail):
            accepted = await asyncio.to_thread(store.progress, job, detail)
            if not accepted:
                raise RuntimeError("Task lease lost during agent workflow")

        if job["agent"] == "assignment-review-team":
            execution = plugin.execute(job["payload"], progress=progress)
        else:
            execution = plugin.execute(job["payload"])
        result = await asyncio.wait_for(execution, timeout=settings.task_timeout)
        accepted = await asyncio.to_thread(store.finish, job, result=result)
        log.info(json.dumps({"event": "task_completed", "task_id": job["id"],
                             "attempt": job["attempts"], "accepted": accepted}))
    except asyncio.CancelledError:
        # The lease recovers work if the process cannot finish during shutdown.
        raise
    except Exception as exc:
        retryable = not isinstance(exc, (ValueError, KeyError))
        error = "Task timed out" if isinstance(exc, TimeoutError) else "Agent execution failed"
        await asyncio.to_thread(store.finish, job, error=error, retryable=retryable)
        log.warning(json.dumps({"event": "task_failed", "task_id": job["id"],
                                "attempt": job["attempts"], "error_type": type(exc).__name__}))


async def run():
    settings = Settings.from_env()
    # Both existing agent modules honor this explicit setting.
    os.environ["AGENT_MODE"] = settings.mode
    if settings.mode == "cloud" and not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("Cloud mode requires GEMINI_API_KEY")
    store = Store(settings.database_url)
    if not store.ready():
        raise RuntimeError("Run python -m cloud.store first")
    enabled = [x.strip() for x in os.getenv("WORKER_AGENTS", ",".join(PLUGINS)).split(",")]
    if not enabled or any(x not in PLUGINS for x in enabled):
        raise ValueError("WORKER_AGENTS contains an unknown agent")
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    heartbeat_path = Path(os.getenv("HEARTBEAT_PATH", "/tmp/worker-heartbeat"))

    async def heartbeat():
        while not stop.is_set():
            await asyncio.to_thread(store.heartbeat, worker_id, enabled)
            # Used by the container healthcheck; only refreshed after a successful DB write.
            if os.name != "nt":
                heartbeat_path.touch()
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass

    pulse = asyncio.create_task(heartbeat())
    try:
        while not stop.is_set():
            if pulse.done():
                await pulse  # Database/heartbeat failure must fail the container.
            job = await asyncio.to_thread(store.claim, worker_id, enabled, settings.lease_seconds)
            if job:
                await execute_job(store, settings, job)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
                except TimeoutError:
                    pass
    finally:
        stop.set()
        await pulse
        store.engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run())
