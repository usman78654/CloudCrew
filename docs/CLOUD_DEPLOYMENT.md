# Cloud deployment and operations

## Portable deployment

The smallest supported topology is one Linux VM running the Compose stack. This uses the same Docker image and PostgreSQL schema as local development. No provider account, resource, domain, or paid model has been provisioned by this repository.

1. Provision a Linux host with Docker Engine and the Compose plugin.
2. Clone the repository and run `python3 scripts/bootstrap.py`.
3. Start `docker compose --env-file .env.cloud up --build --wait --scale worker=2`.
4. For a private demo, use an SSH tunnel to the loopback-bound API port.
5. For public access, configure the HTTPS overlay below.

Choose capacity based on measured memory use and workload. The Compose file limits each API container to 256 MiB and each worker to 384 MiB; the host also needs memory for PostgreSQL, Docker and the OS. These limits are configuration values, not measured capacity claims.

## HTTPS on a public domain

Point a domain's DNS record at the VM. Add these values to `.env.cloud`:

```dotenv
PUBLIC_DOMAIN=agents.example.com
ACME_EMAIL=you@example.com
```

Allow inbound 80/443 at the cloud firewall, keep PostgreSQL private, and run:

```bash
docker compose --env-file .env.cloud -f compose.yaml -f compose.production.yaml up --build -d --scale worker=2
```

The overlay adds Caddy with a 64 KiB request-body limit and persistent certificate storage. The API's published port stays bound to loopback. Caddy handles public HTTPS and proxies over the private Compose network. Add request-rate limits at your cloud ingress if exposing a paid model to untrusted clients.

Public certificate issuance requires a reachable domain and ports; it is not exercised by the local mock demo. See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) and [request body limits](https://caddyserver.com/docs/caddyfile/directives/request_body).

## Enabling Gemini

Edit `.env.cloud`:

```dotenv
AGENT_MODE=cloud
GEMINI_API_KEY=your-provider-key
GEMINI_MODEL=gemini-2.5-flash
```

Recreate the API and workers with `docker compose --env-file .env.cloud up -d --force-recreate api worker --scale worker=2`. A cloud worker refuses to start without a key. Model availability depends on your provider account; the model is configurable.

Both showcased agents use async REST requests with the API key in a header, following the [Gemini generateContent API](https://ai.google.dev/api/generate-content). A submitted task goes to Gemini in cloud mode; previously saved history is not included automatically. Mock tests never require a real key. Provider responses and real billing behavior must be validated separately with your own account.

## Scaling

```bash
docker compose --env-file .env.cloud up -d --scale worker=4
```

Each worker handles one task at a time. To deploy on a managed container platform:

- Deploy the image once as the API and separately with command `python -m cloud.worker`.
- Run `python -m cloud.store` as a one-off initialization job before replicas start.
- Configure a shared `DATABASE_URL=postgresql+psycopg://...` using the platform's secret store.
- Use a managed PostgreSQL service and its required TLS connection parameters.
- Configure HTTP probes for the API and heartbeat checks for workers.
- Allow at least 70 seconds for graceful worker termination with the default 60-second task timeout.

The provided Compose file publishes a fixed API port, so it is intended to scale workers. API replicas require a platform load balancer or an adjusted ingress/port configuration.

## Monitoring and recovery

```bash
docker compose --env-file .env.cloud ps
docker compose --env-file .env.cloud logs --tail=100 api worker
```

The console shows live workers, queue/retry count and completed tasks. Authenticated `/metrics` exposes current task counts by state for the calling owner and the number of live workers. It is not a latency histogram or a throughput benchmark.

If workers stop, the API continues to queue work. Restart workers to resume queued work. Running jobs become eligible for recovery after the lease expires. Dead jobs retain their error and event history.

Container health checks report health; Docker's restart policy restarts exited processes, not merely unhealthy containers. The worker exits if its heartbeat task fails, although an in-flight task may finish first.

## Backups and upgrades

The named PostgreSQL volume persists across `docker compose down`. Do not remove the volume when you want to retain history. Use PostgreSQL backups or managed-provider snapshots and verify restoration into a separate environment.

For a local SQL-format backup inside the database container:

```bash
docker compose --env-file .env.cloud exec -T db pg_dump -U agents -d agents -f /tmp/agents-backup.sql
docker compose --env-file .env.cloud cp db:/tmp/agents-backup.sql ./agents-backup.sql
```

Treat backups as private user data; transfer them to protected storage outside the repository. Schema initialization creates version 1. Future schema changes must introduce explicit migrations; table creation is not a schema-upgrade strategy.

## PostgreSQL test database

The normal deployment does not publish PostgreSQL. For local integration testing only:

```bash
docker compose --env-file .env.cloud -f compose.yaml -f compose.test.yaml up -d db
docker compose --env-file .env.cloud exec -T db createdb -U agents cloud_tests
```

Set `TEST_DATABASE_URL` to `postgresql+psycopg://agents:<POSTGRES_PASSWORD>@127.0.0.1:15432/cloud_tests` and run `python -m pytest tests/cloud -q` in the Python environment. Use the generated password from `.env.cloud`. Do not reuse the application database for the test suite.

GitHub Actions provisions a separate PostgreSQL service for these tests. The test overlay binds only loopback and should not be used for the public deployment.
