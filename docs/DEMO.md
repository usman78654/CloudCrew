# Five-minute six-agent workflow demo

## 1. Explain the problem

CloudCrew runs long educational-agent tasks outside the HTTP request. A stateless FastAPI supervisor creates durable work, PostgreSQL stores the queue and results, and independently deployable workers execute only the roles they advertise.

## 2. Start the system

```bash
python scripts/bootstrap.py
docker compose --env-file .env.cloud up --build --wait
```

Open **http://localhost:8000** and connect with the demo key from `.env.cloud`. The console should show six live worker containers:

- Assignment Coach
- Research Scout
- Study Scheduler
- Citation Manager
- Originality Preflight
- Gemini Reviewer

They use one immutable image with different `WORKER_AGENTS` configuration.

## 3. Show one real collaboration graph

Select **Assignment Success Team** and submit:

> Write a structured academic report about how cloud computing affects education. It is due in seven days and requires verified academic sources in APA format.

Include a short draft for the originality preflight. Open the final task and show the workflow panel:

```text
coach -> research -> citation ----┐
      -> schedule ----------------┼-> review
      -> originality -------------┘
```

Explain that each box is a separate persisted job. Research, Scheduling, and Originality become claimable after Coach succeeds. Citation depends on Research. Reviewer receives the five stored upstream results. A failed dependency prevents downstream work from producing a misleading success.

In mock mode, describe the results as deterministic demonstrations. Research produces search strategies without claiming to retrieve real papers. Originality is a local preflight, not a database-backed plagiarism verdict. In cloud mode, the configured Gemini model generates each specialist response.

## 4. Submit concurrent workflows

```bash
python scripts/workflow_demo.py --workflows 3
```

This submits three parent workflows at once, creating 18 durable stage jobs. Show queued, running, and succeeded counts changing in the terminal and dashboard. Point out each worker's advertised role and the worker ID saved on each claimed stage.

## 5. Connect the demo to cloud and DevOps concepts

Use what is visible on screen:

- One immutable container image with configuration-driven worker roles
- Horizontal scaling through additional replicas of a busy role
- Stateless API and asynchronous `202 Accepted` submission
- PostgreSQL durable queue and transactional `FOR UPDATE SKIP LOCKED` claims
- Dependency-aware workflow orchestration and parallel branches
- Heartbeats, time-limited leases, retries, and stale-result fencing
- Idempotency keys and owner-scoped authentication
- Health checks, read-only containers, non-root execution, and CI
- Queue depth, failures, retries, worker presence, queue wait, and execution metrics

Execution is at least once. Fencing prevents a stale worker from overwriting a newer result, but it cannot reverse an external model call that already happened.

## 6. State the project boundary accurately

The repository's original agents are educational and Google Classroom-style tools. The new work is the cloud runtime, six-stage durable coordination, role-specific workers, workflow dashboard, recovery controls, tests, and CI. This version does not claim a live Google Classroom integration or public-cloud deployment.

## CV wording supported by this implementation

**Distributed Multi-Agent Education Platform | FastAPI, Python, Docker, PostgreSQL, LangGraph**

- Built a stateless supervisor that atomically schedules a six-agent educational workflow as a durable dependency graph with parallel branches and persisted handoffs.
- Deployed role-specific workers from one immutable container image with transactional claiming, idempotency, bounded retries, heartbeats, lease recovery, and stale-result fencing.
- Added an operations console, Prometheus-format metrics, concurrent workflow demonstrations, PostgreSQL integration tests, and containerized CI.
