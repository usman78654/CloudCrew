import uuid

import pytest
from fastapi.testclient import TestClient

from cloud.api import create_app
from cloud.config import Settings

KEY = "a" * 40
OTHER_KEY = "b" * 40


@pytest.fixture
def client(store, owner):
    settings = Settings(api_keys={owner: KEY, owner + "-other": OTHER_KEY})
    with TestClient(create_app(settings, store)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        yield client


def payload():
    return {"agent": "gemini-wrapper", "payload": {"request": "Explain distributed queues"}}


def test_authentication_and_health(client):
    assert client.get("/health/live", headers={"Authorization": ""}).status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/v1/tasks", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/").status_code == 200


def test_submit_poll_events_and_isolation(client):
    response = client.post("/api/v1/tasks", json=payload())
    assert response.status_code == 202
    job = response.json()
    assert "lease_token" not in job and "owner" not in job
    location = response.headers["Location"]
    assert client.get(location).json()["status"] == "queued"
    assert client.get(location + "/events").json()[0]["status"] == "queued"
    other = {"Authorization": "Bearer " + OTHER_KEY}
    assert client.get(location, headers=other).status_code == 404
    assert client.get(location + "/events", headers=other).status_code == 404
    assert client.post(location + "/cancel", headers=other).status_code == 404
    assert client.get("/api/v1/tasks", headers=other).json() == []
    assert client.post(location + "/cancel").json()["status"] == "cancelled"


def test_idempotency_and_validation(client):
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    assert client.post("/api/v1/tasks", json=payload(), headers=headers).status_code == 202
    assert client.post("/api/v1/tasks", json=payload(), headers=headers).status_code == 200
    changed = payload()
    changed["payload"]["request"] = "changed"
    assert client.post("/api/v1/tasks", json=changed, headers=headers).status_code == 409
    assert client.post("/api/v1/tasks", json={"agent": "unknown", "payload": {}}).status_code == 422
    assert client.post("/api/v1/tasks", json={"payload": {"request": "x" * 8001}}).status_code == 422


def test_auto_route_and_conversation_history(client):
    body = {"payload": {"request": "Help plan my assignment"}, "conversation_id": "coursework"}
    response = client.post("/api/v1/tasks", json=body)
    assert response.status_code == 202
    assert response.json()["agent"] == "assignment-coach"
    assert len(client.get("/api/v1/tasks?conversation_id=coursework").json()) == 1
    assert client.get("/api/v1/tasks?conversation_id=unrelated").json() == []


def test_operations_and_metrics(client):
    assert client.get("/api/v1/operations").status_code == 200
    response = client.get("/metrics")
    assert response.status_code == 200
    assert 'agent_tasks{status="queued"}' in response.text
    assert client.get("/metrics", headers={"Authorization": ""}).status_code == 401


def test_configuration_rejects_insecure_or_invalid_values():
    with pytest.raises(ValueError):
        Settings(api_keys={"demo": "short"})
    with pytest.raises(ValueError):
        Settings(task_timeout=90, lease_seconds=60)
    with pytest.raises(ValueError):
        Settings(mode="auto")
