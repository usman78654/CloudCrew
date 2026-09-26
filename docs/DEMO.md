# Five-minute project walkthrough

## 1. Explain the problem

Long-running agent calls should not tie task ownership to an HTTP request or a single server process. This project accepts work quickly and executes it using independent workers with persistent state.

## 2. Start the system

```bash
python scripts/bootstrap.py
docker compose --env-file .env.cloud up --build --wait --scale worker=2
```

Open the console and connect using the demo key from `.env.cloud`. Show the two live workers and MOCK execution mode.

## 3. Submit a useful task

Select Assignment Coach and request a plan for a cloud-systems assignment. Open the completed task. Explain the four-step plan, the persisted result, the attempt count and the event timeline.

Use the API docs to show typed inputs, bearer authentication, idempotency keys and the asynchronous 202 response.

Choose **Assignment Review Team** for a visible handoff: Assignment Coach creates its plan, Gemini Reviewer receives that structured plan, and the task event history records both steps. In mock mode the final review explains that it inspected the generated steps and estimates. The review is deterministic mock output, not a paid LLM judgment.

## 4. Measure local performance

Keep execution mode set to **MOCK** and run:

```bash
python scripts/load_test.py --tasks 100 --clients 12
```

The report includes throughput and p50/p95 request, queue-wait and execution measurements. The checked-in one/two-worker comparison is in [`benchmarks/README.md`](benchmarks/README.md); it records the machine and mock configuration and explains why two workers did not double throughput. To reproduce it, use the matching `MOCK_LATENCY_MS=100` and separate `--output` files as shown in the README. These figures describe your computer.

## 5. Demonstrate worker recovery

With two mock workers running:

```bash
python scripts/worker_failure_demo.py
```

The script kills the worker currently processing a task, waits for its short lease to expire, then prints the retry attempt and `Recovered expired worker lease` event. It restores the worker count and lease settings it found before the demo. No Gemini API key or model call is needed.

## 6. Demonstrate durability

Stop workers, submit another task, and observe it queued:

```bash
docker compose --env-file .env.cloud stop worker
```

Restart the API. Reconnect to the console; existing tasks are still present:

```bash
docker compose --env-file .env.cloud restart api
```

Start workers again and watch the queued task complete:

```bash
docker compose --env-file .env.cloud start worker
```

Use the worker-failure script above to demonstrate stale-worker fencing with a controlled mock delay and short lease.

## 7. Discuss the engineering tradeoffs

- PostgreSQL queue: fewer services, polling overhead.
- At-least-once execution: retries restore progress, external calls may repeat.
- Lease tokens: only the current worker may publish a result.
- SQLite: lightweight local development; PostgreSQL: container and multi-host deployment.
- API keys and tenant isolation: a simple authentication boundary, with an identity provider as a future extension.

## CV wording supported by this implementation

**Multi-Agent Distributed Backend | FastAPI, Python, Docker, PostgreSQL, SQLite, LangGraph**

- Built a stateless supervisor and independently scalable workers with durable task routing, idempotent submissions, bounded retries and lease-based crash recovery.
- Integrated Gemini Wrapper and Assignment Coach plugins with explicit mock/cloud modes, async model calls, task planning and owner-scoped persistent history.
- Containerized the platform with health checks, a monitoring console and CI covering concurrency, authentication, recovery and API-restart integration tests.

Describe it as cloud-portable until you have actually deployed it to a cloud provider. Add throughput or latency numbers only after a reproducible benchmark.
