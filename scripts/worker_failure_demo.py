"""Kill a mock worker during work; observe the other worker reclaim its lease."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
LEASE_SECONDS = "8"
TASK_TIMEOUT = "6"
MOCK_LATENCY_MS = "2500"


def docker(*args, env=None):
    command = ["docker", "compose", "--env-file", ".env.cloud", *args]
    return subprocess.run(command, cwd=ROOT, env=env, check=True,
                          text=True, capture_output=True).stdout.strip()


def main():
    env_file = ROOT / ".env.cloud"
    values = dotenv_values(env_file)
    keys = json.loads(values.get("API_KEYS") or "{}")
    api_key = next(iter(keys.values()), None)
    if not api_key:
        raise SystemExit("Create .env.cloud first with python scripts/bootstrap.py")
    base_url = "http://127.0.0.1:8000"
    headers = {"Authorization": "Bearer " + api_key}
    client = httpx.Client(base_url=base_url, headers=headers, timeout=10)
    initial_workers = docker("ps", "-q", "worker").splitlines()
    if len(initial_workers) < 2:
        raise SystemExit("Start the demo first with docker compose --env-file .env.cloud up -d --scale worker=2")
    settings = ("TASK_TIMEOUT", "LEASE_SECONDS", "MOCK_LATENCY_MS")
    original_env = json.loads(subprocess.run(
        ["docker", "exec", initial_workers[0], "python", "-c",
         "import json,os;print(json.dumps({k:os.getenv(k) for k in " + repr(settings) + "}))"],
        check=True, text=True, capture_output=True,
    ).stdout)

    compose_env = dict(os.environ)
    try:
        mode = client.get("/api/v1/operations").raise_for_status()
        if mode.json()["mode"] != "mock":
            raise SystemExit("Worker-failure demo requires AGENT_MODE=mock (no provider calls).")

        demo_env = dict(compose_env, TASK_TIMEOUT=TASK_TIMEOUT,
                        LEASE_SECONDS=LEASE_SECONDS, MOCK_LATENCY_MS=MOCK_LATENCY_MS)
        print("Starting two mock workers with an 8-second lease and 2.5-second tasks.")
        docker("up", "--detach", "--no-build", "--scale", f"worker={len(initial_workers)}",
               "--force-recreate", "worker", env=demo_env)

        conversation = "worker-failure-" + uuid.uuid4().hex[:8]
        task_ids = []
        for index in range(max(6, 2 * len(initial_workers))):
            response = client.post("/api/v1/tasks", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
                "agent": "gemini-wrapper", "conversation_id": conversation,
                "payload": {"request": f"Recovery demo task {index + 1}: explain a task lease."},
            })
            response.raise_for_status()
            task_ids.append(response.json()["id"])

        deadline = time.monotonic() + 20
        running = None
        while time.monotonic() < deadline and running is None:
            for task_id in task_ids:
                job = client.get("/api/v1/tasks/" + task_id).raise_for_status().json()
                if job["status"] == "running":
                    running = job
                    break
            if running is None:
                time.sleep(.1)
        if running is None:
            raise RuntimeError("No demo task was claimed by a worker")

        target = None
        for container in docker("ps", "-q", "worker").splitlines():
            hostname = subprocess.run(
                ["docker", "inspect", "--format", "{{.Config.Hostname}}", container],
                check=True, text=True, capture_output=True,
            ).stdout.strip()
            if str(running.get("worker_id", "")).startswith(hostname):
                target = container
                break
        if target is None:
            raise RuntimeError("Could not match the claimed task to a worker container")

        print("Stopping the worker that owns a running task...")
        subprocess.run(["docker", "kill", target], check=True, text=True, capture_output=True)
        deadline = time.monotonic() + 60
        recovered = None
        while time.monotonic() < deadline:
            recovered = client.get("/api/v1/tasks/" + running["id"]).raise_for_status().json()
            if recovered["status"] == "succeeded" and recovered["attempts"] >= 2:
                break
            time.sleep(.3)
        if not recovered or recovered["status"] != "succeeded" or recovered["attempts"] < 2:
            raise RuntimeError(f"Worker did not recover the killed task: {recovered}")

        other_ids = [task_id for task_id in task_ids if task_id != running["id"]]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            states = [client.get("/api/v1/tasks/" + task_id).raise_for_status().json()["status"]
                      for task_id in other_ids]
            if all(state in {"succeeded", "dead", "cancelled"} for state in states):
                break
            time.sleep(.3)
        timeline = client.get("/api/v1/tasks/" + running["id"] + "/events").raise_for_status().json()
        print(f"Recovered task after {recovered['attempts']} attempts:")
        for event in timeline:
            print(f"  {event['status']:9} {event['detail']}")
        print(f"Other queued tasks: {sum(state == 'succeeded' for state in states)}/{len(states)} succeeded.")
    finally:
        print("Restoring the original worker count and lease settings...")
        restore_env = dict(compose_env)
        for name, value in original_env.items():
            if value is None:
                restore_env.pop(name, None)
            else:
                restore_env[name] = str(value)
        docker("up", "--detach", "--no-build", "--scale", f"worker={len(initial_workers)}",
               "--force-recreate", "worker", env=restore_env)
        client.close()


if __name__ == "__main__":
    main()
