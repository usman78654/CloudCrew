"""End-to-end demonstration against a running API and separate workers."""
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env.cloud")
keys = json.loads(os.environ["API_KEYS"])
key = os.getenv("DEMO_API_KEY") or next(iter(keys.values()))
base = os.getenv("API_URL", "http://127.0.0.1:8000")
with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + key}, timeout=10) as client:
    response = client.post("/api/v1/tasks", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "agent": "assignment-review-team",
        "conversation_id": "recruiter-demo",
        "payload": {"assignment_title": "Design a fault-tolerant cloud task system",
                    "subject": "Distributed Systems"},
    })
    response.raise_for_status()
    task = response.json()
    deadline = time.monotonic() + 180
    while task["status"] not in {"succeeded", "dead", "cancelled"}:
        if time.monotonic() > deadline:
            raise SystemExit("Timed out waiting for a worker. Check /api/v1/operations.")
        time.sleep(1)
        response = client.get("/api/v1/tasks/" + task["id"])
        response.raise_for_status()
        task = response.json()
    print(json.dumps(task, indent=2))
    timeline = client.get("/api/v1/tasks/" + task["id"] + "/events")
    timeline.raise_for_status()
    print(json.dumps(timeline.json(), indent=2))
    if task["status"] != "succeeded":
        raise SystemExit("Demo task did not succeed")
