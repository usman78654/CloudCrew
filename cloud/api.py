"""Stateless supervisor API; workers are separate processes."""
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from cloud.agents import PLUGINS, route
from cloud.config import Settings
from cloud.store import Conflict, Store

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cloud.api")
bearer = HTTPBearer(auto_error=False)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = Field(default="auto", max_length=100)
    payload: dict
    conversation_id: str = Field(default="default", min_length=1, max_length=100)


def public_job(job):
    return {key: value for key, value in job.items()
            if key not in {"owner", "fingerprint", "lease_token", "idempotency_key"}}


def create_app(settings=None, store=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env()
        if not app.state.settings.api_keys:
            raise RuntimeError("Configure API_KEYS before starting the API (see scripts/bootstrap.py)")
        app.state.store = store or Store(app.state.settings.database_url)
        if not app.state.store.ready():
            raise RuntimeError("Run python -m cloud.store before starting the API")
        yield
        if store is None:
            app.state.store.engine.dispose()

    app = FastAPI(title="Multi-Agent Cloud Supervisor", version="2.0.0", lifespan=lifespan)

    def owner(request: Request, credential: HTTPAuthorizationCredentials = Depends(bearer)):
        if credential and credential.scheme.lower() == "bearer":
            for identity, key in request.app.state.settings.api_keys.items():
                if hmac.compare_digest(credential.credentials.encode(), key.encode()):
                    return identity
        raise HTTPException(401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"})

    @app.middleware("http")
    async def request_log(request, call_next):
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        log.info(json.dumps({"event": "http_request", "request_id": request_id,
                             "method": request.method, "status": response.status_code,
                             "duration_ms": round((time.perf_counter() - start) * 1000, 2)}))
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        log.error(json.dumps({"event": "database_unavailable", "type": type(exc).__name__}))
        return JSONResponse(status_code=503, content={"detail": "Database unavailable; retry later"})

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(Path(__file__).with_name("dashboard.html"))

    @app.get("/health/live", tags=["Operations"])
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["Operations"])
    def ready(request: Request, response: Response):
        try:
            healthy = request.app.state.store.ready()
        except SQLAlchemyError:
            healthy = False
        response.status_code = 200 if healthy else 503
        return {"status": "ready" if healthy else "unavailable"}

    @app.get("/api/v1/agents", tags=["Agents"])
    def agents(identity=Depends(owner)):
        return [{"id": key, "name": p.name, "description": p.description,
                 "input_schema": p.input_model.model_json_schema()} for key, p in PLUGINS.items()]

    @app.post("/api/v1/tasks", status_code=202, tags=["Tasks"])
    def submit(body: Submission, request: Request, response: Response,
               identity=Depends(owner),
               idempotency_key: str | None = Header(default=None, min_length=1, max_length=128)):
        agent, payload = body.agent, body.payload
        if agent == "auto":
            from cloud.agents import GeminiInput
            try:
                text_input = GeminiInput.model_validate(payload)
            except ValidationError:
                raise HTTPException(422, "Auto routing requires a request string of 1 to 8000 characters")
            agent, payload = route(text_input.request)
        if agent not in PLUGINS:
            raise HTTPException(422, "Unknown agent")
        try:
            payload = PLUGINS[agent].input_model.model_validate(payload).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_input=False, include_url=False)))
        try:
            job, created = request.app.state.store.submit(
                identity, agent, payload, body.conversation_id, idempotency_key,
                request.app.state.settings.max_attempts,
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        response.status_code = 202 if created else 200
        response.headers["Location"] = "/api/v1/tasks/" + job["id"]
        return public_job(job)

    @app.get("/api/v1/tasks", tags=["Tasks"])
    def history(request: Request, conversation_id: str | None = Query(default=None, max_length=100),
                limit: int = Query(default=50, ge=1, le=100), identity=Depends(owner)):
        return [public_job(job) for job in request.app.state.store.history(identity, conversation_id, limit)]

    @app.get("/api/v1/tasks/{job_id}", tags=["Tasks"])
    def task(job_id: uuid.UUID, request: Request, identity=Depends(owner)):
        job = request.app.state.store.get(str(job_id), identity)
        if job is None:
            raise HTTPException(404, "Task not found")
        return public_job(job)

    @app.get("/api/v1/tasks/{job_id}/events", tags=["Tasks"])
    def timeline(job_id: uuid.UUID, request: Request, identity=Depends(owner)):
        result = request.app.state.store.timeline(str(job_id), identity)
        if result is None:
            raise HTTPException(404, "Task not found")
        return result

    @app.post("/api/v1/tasks/{job_id}/cancel", tags=["Tasks"])
    def cancel(job_id: uuid.UUID, request: Request, identity=Depends(owner)):
        db = request.app.state.store
        if db.get(str(job_id), identity) is None:
            raise HTTPException(404, "Task not found")
        if not db.cancel(str(job_id), identity):
            raise HTTPException(409, "Task is already terminal")
        return public_job(db.get(str(job_id), identity))

    @app.get("/api/v1/operations", tags=["Operations"])
    def operations(request: Request, identity=Depends(owner)):
        db = request.app.state.store
        return {"telemetry": db.telemetry(identity), "workers": db.worker_status(),
                "mode": request.app.state.settings.mode}

    @app.get("/metrics", tags=["Operations"])
    def metrics(request: Request, identity=Depends(owner)):
        data = request.app.state.store.telemetry(identity)
        counts = data["tasks"]
        lines = ["# HELP agent_tasks Tasks for the authenticated owner by state.",
                 "# TYPE agent_tasks gauge"]
        for state in ("queued", "running", "retry", "succeeded", "dead", "cancelled"):
            lines.append(f'agent_tasks{{status="{state}"}} {counts.get(state, 0)}')
        lines.extend(["# HELP agent_queue_oldest_seconds Age of the oldest queued or retrying task.",
                      "# TYPE agent_queue_oldest_seconds gauge",
                      f"agent_queue_oldest_seconds {data['oldest_queue_seconds']:.3f}",
                      "# HELP agent_task_retries_total Additional task attempts caused by retries or recovery.",
                      "# TYPE agent_task_retries_total counter",
                      f"agent_task_retries_total {data['retries_total']}",
                      "# HELP agent_task_failed_attempts_total Attempts that ended in retry or terminal failure.",
                      "# TYPE agent_task_failed_attempts_total counter",
                      f"agent_task_failed_attempts_total {data['failed_attempts_total']}",
                      "# HELP agent_task_terminal_failures_total Tasks exhausted after all attempts.",
                      "# TYPE agent_task_terminal_failures_total counter",
                      f"agent_task_terminal_failures_total {data['failed_tasks_total']}",
                      "# HELP agent_task_failure_rate_percent Terminal task failure rate.",
                      "# TYPE agent_task_failure_rate_percent gauge",
                      f"agent_task_failure_rate_percent {data['failure_rate_percent']:.2f}",
                      "# HELP agent_tasks_completed_last_5m Tasks completed within five minutes.",
                      "# TYPE agent_tasks_completed_last_5m gauge",
                      f"agent_tasks_completed_last_5m {data['completed_last_5m']}",
                      "# HELP agent_worker_timing_seconds Mean and p95 timing for the latest 1000 completed tasks.",
                      "# TYPE agent_worker_timing_seconds gauge"])
        for metric, label in (("queue_wait", "queue wait"), ("execution", "execution")):
            average_ms = data[f"avg_{metric}_ms"]
            p95_ms = data[f"p95_{metric}_ms"]
            lines.extend([
                f"agent_worker_timing_seconds{{phase=\"{label}\",statistic=\"mean\"}} {average_ms / 1000:.6f}",
                f"agent_worker_timing_seconds{{phase=\"{label}\",statistic=\"p95\"}} {p95_ms / 1000:.6f}",
            ])
        lines.extend(["# HELP agent_worker_timing_sample_size Number of completed task samples used for percentiles.",
                      "# TYPE agent_worker_timing_sample_size gauge",
                      f"agent_worker_timing_sample_size {data['percentile_sample_size']}"])
        lines.extend(["# HELP agent_workers_live Workers with a recent heartbeat.",
                      "# TYPE agent_workers_live gauge",
                      f"agent_workers_live {len(request.app.state.store.worker_status())}"])
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    return app


app = create_app()
