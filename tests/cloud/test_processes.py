"""A real HTTP API and two independent worker processes, with API restart."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def wait_until(check, processes, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert all(p.poll() is None for p in processes), "A service exited unexpectedly"
        try:
            result = check()
            if result:
                return result
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(0.2)
    raise AssertionError("Services did not reach the expected state")


def test_api_and_independent_workers_survive_api_restart(tmp_path):
    root = Path(__file__).resolve().parents[2]
    key = "integration-key-" + "x" * 32
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, DATABASE_URL="sqlite:///" + (tmp_path / "process.db").as_posix(),
               API_KEYS=json.dumps({"process-test": key}), AGENT_MODE="mock", POLL_SECONDS="0.1",
               HEARTBEAT_PATH=str(tmp_path / "heartbeat"))
    subprocess.run([sys.executable, "-m", "cloud.store"], cwd=root, env=env, check=True, capture_output=True)
    api_command = [sys.executable, "-m", "uvicorn", "cloud.api:app", "--host", "127.0.0.1", "--port", str(port)]
    processes = []
    log_path = tmp_path / "services.log"
    with log_path.open("w", encoding="utf-8") as log:
        def launch(command):
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
            processes.append(process)
            return process
        try:
            api = launch(api_command)
            launch([sys.executable, "-m", "cloud.worker"])
            launch([sys.executable, "-m", "cloud.worker"])
            with httpx.Client(base_url=f"http://127.0.0.1:{port}",
                              headers={"Authorization": "Bearer " + key}, timeout=2) as client:
                wait_until(lambda: client.get("/health/ready").status_code == 200, processes)
                wait_until(lambda: len(client.get("/api/v1/operations").json()["workers"]) == 2, processes)
                ids = []
                for agent, payload in [
                    ("gemini-wrapper", {"request": "Explain task leases"}),
                    ("assignment-coach", {"assignment_title": "Design cloud recovery"}),
                ]:
                    response = client.post("/api/v1/tasks", json={"agent": agent, "payload": payload})
                    assert response.status_code == 202
                    ids.append(response.json()["id"])
                for job_id in ids:
                    wait_until(lambda: client.get("/api/v1/tasks/" + job_id).json()["status"] == "succeeded", processes)
                api.terminate()
                api.wait(timeout=10)
                processes.remove(api)
                launch(api_command)
                wait_until(lambda: client.get("/health/ready").status_code == 200, processes)
                for job_id in ids:
                    task = client.get("/api/v1/tasks/" + job_id).json()
                    assert task["status"] == "succeeded" and task["result"]
                    assert len(client.get("/api/v1/tasks/" + job_id + "/events").json()) == 3
        except BaseException:
            log.flush()
            print(log_path.read_text(encoding="utf-8"))
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
