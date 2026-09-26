"""Measure submitted-task throughput and timing against the running mock stack."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import time
import uuid

import httpx
from dotenv import dotenv_values


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=100, help="Tasks to enqueue (1-1000)")
    parser.add_argument("--clients", type=int, default=12, help="Concurrent API submitters (1-64)")
    parser.add_argument("--conversation", default="", help="Optional conversation label")
    parser.add_argument("--mock-latency-ms", type=int, default=0,
                        help="Configured MOCK_LATENCY_MS, for reproducible load reports")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/benchmarks/local-load-results.json")
    args = parser.parse_args()
    if not 1 <= args.tasks <= 1000 or not 1 <= args.clients <= 64:
        parser.error("--tasks must be 1-1000 and --clients must be 1-64")

    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env.cloud")
    keys = json.loads(config.get("API_KEYS") or "{}")
    key = next(iter(keys.values()), None)
    if not key:
        parser.error("Generate .env.cloud first with python scripts/bootstrap.py")

    base_url = args.url.rstrip("/")
    headers = {"Authorization": "Bearer " + key}
    conversation = args.conversation or "load-" + uuid.uuid4().hex[:10]
    ids = []
    submitted = {}
    wall_start = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()

    def submit(index):
        request = f"Load sample {index + 1}: explain why durable task queues use worker leases."
        begin = time.perf_counter()
        response = httpx.post(
            base_url + "/api/v1/tasks", headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={"agent": "gemini-wrapper", "conversation_id": conversation,
                  "payload": {"request": request}}, timeout=20,
        )
        response.raise_for_status()
        return response.json()["id"], time.perf_counter() - begin

    enqueue_latencies = []
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        for future in as_completed([pool.submit(submit, i) for i in range(args.tasks)]):
            job_id, enqueue_time = future.result()
            ids.append(job_id)
            submitted[job_id] = time.perf_counter()
            enqueue_latencies.append(enqueue_time)

    done = {}
    deadline = time.monotonic() + 600
    with httpx.Client(base_url=base_url, headers=headers, timeout=10) as client:
        while len(done) < args.tasks:
            if time.monotonic() > deadline:
                raise SystemExit(f"Timed out: {len(done)}/{args.tasks} tasks reached a terminal state")
            for start in range(0, len(ids), 50):
                for job_id in ids[start:start + 50]:
                    if job_id in done:
                        continue
                    response = client.get("/api/v1/tasks/" + job_id)
                    response.raise_for_status()
                    task = response.json()
                    if task["status"] in {"succeeded", "dead", "cancelled"}:
                        done[job_id] = (task, time.perf_counter() - submitted[job_id])
            if len(done) < args.tasks:
                time.sleep(0.15)

    elapsed = time.perf_counter() - wall_start
    results = [item[0] for item in done.values()]
    response_times = [item[1] for item in done.values()]
    successes = sum(task["status"] == "succeeded" for task in results)
    queue_samples = [task["queue_wait_ms"] for task in results
                     if task["status"] == "succeeded" and task.get("queue_wait_ms") is not None]
    execution_samples = [task["execution_ms"] for task in results
                         if task["status"] == "succeeded" and task.get("execution_ms") is not None]
    ops_response = httpx.get(base_url + "/api/v1/operations", headers=headers, timeout=10)
    ops_response.raise_for_status()
    worker_count = len(ops_response.json()["workers"])
    record = {
        "started_at_utc": started,
        "mode": "mock",
        "configured_mock_latency_ms": args.mock_latency_ms,
        "host_os": platform.system(),
        "logical_cpus": __import__("os").cpu_count(),
        "tasks_requested": args.tasks,
        "tasks_succeeded": successes,
        "tasks_failed_or_cancelled": args.tasks - successes,
        "submit_clients": args.clients,
        "worker_count": worker_count,
        "elapsed_seconds": round(elapsed, 3),
        "completed_tasks_per_second": round(successes / elapsed, 3) if elapsed else 0,
        "submit_latency_ms": {"p50": percentile([x * 1000 for x in enqueue_latencies], .50),
                              "p95": percentile([x * 1000 for x in enqueue_latencies], .95)},
        "submit_to_result_seconds": {"p50": percentile(response_times, .50),
                                      "p95": percentile(response_times, .95)},
        "database_queue_wait_ms": {"p50": percentile(queue_samples, .50),
                                    "p95": percentile(queue_samples, .95)},
        "worker_execution_ms": {"p50": percentile(execution_samples, .50),
                                 "p95": percentile(execution_samples, .95)},
        "conversation_id": conversation,
        "limitations": "Local machine, Docker Compose, mock agent; not a cloud capacity benchmark.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    print(f"Saved results to {output}")


if __name__ == "__main__":
    main()
