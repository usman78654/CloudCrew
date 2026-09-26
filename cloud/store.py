"""Transactional queue. PostgreSQL for replicas; SQLite for single-host development."""
import hashlib
import json
import math
import time
import uuid
from contextlib import contextmanager

from sqlalchemy import (
    JSON, Column, Float, Index, Integer, MetaData, String, Table,
    UniqueConstraint, and_, case, create_engine, delete, event, func, insert, or_,
    inspect, select, text, update,
)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
jobs = Table(
    "cloud_jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("owner", String(128), nullable=False),
    Column("conversation_id", String(100), nullable=False),
    Column("agent", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("idempotency_key", String(128)),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("available_at", Float, nullable=False),
    Column("queue_wait_ms", Float),
    Column("execution_ms", Float),
    Column("lease_until", Float),
    Column("lease_token", String(36)),
    Column("worker_id", String(100)),
    Column("result", JSON),
    Column("error", String(500)),
    UniqueConstraint("owner", "idempotency_key", name="uq_cloud_job_idempotency"),
)
Index("ix_cloud_queue", jobs.c.status, jobs.c.available_at, jobs.c.created_at)
Index("ix_cloud_owner_history", jobs.c.owner, jobs.c.conversation_id, jobs.c.created_at)
events = Table(
    "cloud_events", metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), nullable=False, index=True),
    Column("at", Float, nullable=False),
    Column("status", String(20), nullable=False),
    Column("detail", String(500), nullable=False),
)
workers = Table(
    "cloud_workers", metadata,
    Column("id", String(100), primary_key=True),
    Column("seen_at", Float, nullable=False),
    Column("agents", JSON, nullable=False),
)
schema = Table("cloud_schema", metadata, Column("version", Integer, primary_key=True))


class Conflict(Exception):
    pass


class Store:
    def __init__(self, url):
        args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=args, pool_pre_ping=True)
        self.sqlite = self.engine.dialect.name == "sqlite"
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

    def migrate(self):
        # Run once before starting replicas. Keep upgrades additive for existing databases.
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            version = conn.scalar(select(schema.c.version))
            if version is None:
                conn.execute(insert(schema).values(version=2))
            elif version == 1:
                existing = {column["name"] for column in inspect(conn).get_columns("cloud_jobs")}
                if "queue_wait_ms" not in existing:
                    conn.execute(text("ALTER TABLE cloud_jobs ADD COLUMN queue_wait_ms FLOAT"))
                if "execution_ms" not in existing:
                    conn.execute(text("ALTER TABLE cloud_jobs ADD COLUMN execution_ms FLOAT"))
                conn.execute(update(schema).values(version=2))

    def ready(self):
        with self.engine.connect() as conn:
            return conn.scalar(select(schema.c.version)) == 2

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            if self.sqlite:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _event(self, conn, job_id, status, detail, now):
        conn.execute(insert(events).values(
            id=str(uuid.uuid4()), job_id=job_id, at=now, status=status, detail=detail,
        ))

    def submit(self, owner, agent, payload, conversation_id, key=None, max_attempts=3):
        fingerprint = hashlib.sha256(json.dumps(
            [agent, payload, conversation_id], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        now = time.time()
        row = dict(id=str(uuid.uuid4()), owner=owner, agent=agent, payload=payload,
                   conversation_id=conversation_id, fingerprint=fingerprint,
                   idempotency_key=key, status="queued", attempts=0,
                   max_attempts=max_attempts, created_at=now, updated_at=now,
                   available_at=now)
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(jobs).values(**row))
                self._event(conn, row["id"], "queued", "Accepted by supervisor", now)
        except IntegrityError:
            if not key:
                raise
            with self.engine.connect() as conn:
                existing = conn.execute(select(jobs).where(
                    jobs.c.owner == owner, jobs.c.idempotency_key == key,
                )).mappings().first()
            if existing is None:
                raise
            if existing["fingerprint"] != fingerprint:
                raise Conflict("Idempotency key already used for a different request")
            return dict(existing), False
        return self.get(row["id"], owner), True

    def get(self, job_id, owner):
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(
                jobs.c.id == job_id, jobs.c.owner == owner,
            )).mappings().first()
            return dict(row) if row else None

    def history(self, owner, conversation_id=None, limit=50):
        query = select(jobs).where(jobs.c.owner == owner)
        if conversation_id is not None:
            query = query.where(jobs.c.conversation_id == conversation_id)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(
                query.order_by(jobs.c.created_at.desc()).limit(limit)
            ).mappings()]

    def timeline(self, job_id, owner):
        if not self.get(job_id, owner):
            return None
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(events).where(
                events.c.job_id == job_id,
            ).order_by(events.c.at, events.c.id)).mappings()]

    def claim(self, worker_id, agents, lease_seconds, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            eligible = or_(
                and_(jobs.c.status.in_(["queued", "retry"]), jobs.c.available_at <= now),
                and_(jobs.c.status == "running", jobs.c.lease_until <= now),
            )
            # Exhausted crashed jobs become dead; keep scanning to avoid starving the queue.
            while True:
                query = select(jobs).where(
                    eligible, jobs.c.agent.in_(agents),
                ).order_by(jobs.c.created_at).limit(1)
                if not self.sqlite:
                    query = query.with_for_update(skip_locked=True)
                row = conn.execute(query).mappings().first()
                if row is None:
                    return None
                row = dict(row)
                if row["attempts"] >= row["max_attempts"]:
                    conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                        status="dead", error="Worker lease expired; retry budget exhausted",
                        lease_token=None, lease_until=None, updated_at=now,
                    ))
                    self._event(conn, row["id"], "dead", "Retry budget exhausted after worker loss", now)
                    continue
                detail = "Recovered expired worker lease" if row["status"] == "running" else "Claimed by worker"
                changes = dict(status="running", attempts=row["attempts"] + 1,
                               worker_id=worker_id, lease_token=str(uuid.uuid4()),
                               lease_until=now + lease_seconds, updated_at=now,
                               queue_wait_ms=row["queue_wait_ms"]
                               if row["queue_wait_ms"] is not None
                               else max(0.0, (now - row["created_at"]) * 1000))
                conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(**changes))
                self._event(conn, row["id"], "running", detail, now)
                row.update(changes)
                return row

    def finish(self, job, result=None, error=None, retryable=True, now=None):
        now = time.time() if now is None else now
        status = "succeeded"
        if error:
            status = "retry" if retryable and job["attempts"] < job["max_attempts"] else "dead"
        with self.engine.begin() as conn:
            changed = conn.execute(update(jobs).where(
                jobs.c.id == job["id"], jobs.c.status == "running",
                jobs.c.lease_token == job["lease_token"], jobs.c.lease_until > now,
            ).values(
                status=status, result=result, error=error, updated_at=now,
                available_at=now + min(2 ** job["attempts"], 60),
                execution_ms=(job["execution_ms"] or 0.0)
                + max(0.0, (now - job["updated_at"]) * 1000),
                lease_token=None, lease_until=None,
            )).rowcount
            if changed:
                self._event(conn, job["id"], status, error or "Result persisted", now)
            return bool(changed)

    def cancel(self, job_id, owner):
        now = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(update(jobs).where(
                jobs.c.id == job_id, jobs.c.owner == owner,
                jobs.c.status.in_(["queued", "retry", "running"]),
            ).values(status="cancelled", updated_at=now, lease_token=None, lease_until=None)).rowcount
            if changed:
                self._event(conn, job_id, "cancelled", "Cancelled by owner; late results discarded", now)
            return bool(changed)

    def heartbeat(self, worker_id, agents):
        now = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(update(workers).where(workers.c.id == worker_id).values(
                seen_at=now, agents=agents,
            )).rowcount
            if not changed:
                conn.execute(insert(workers).values(id=worker_id, seen_at=now, agents=agents))
            conn.execute(delete(workers).where(workers.c.seen_at < now - 86400))

    def worker_status(self):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(workers).where(
                workers.c.seen_at > time.time() - 30,
            )).mappings()]

    def counts(self, owner):
        with self.engine.connect() as conn:
            return dict(conn.execute(select(jobs.c.status, func.count()).where(
                jobs.c.owner == owner,
            ).group_by(jobs.c.status)).all())

    def telemetry(self, owner, now=None):
        now = time.time() if now is None else now
        with self.engine.connect() as conn:
            counts = dict(conn.execute(select(jobs.c.status, func.count()).where(
                jobs.c.owner == owner,
            ).group_by(jobs.c.status)).all())
            oldest = conn.scalar(select(func.min(jobs.c.created_at)).where(
                jobs.c.owner == owner, jobs.c.status.in_(["queued", "retry"]),
            ))
            aggregates = conn.execute(select(
                func.count(jobs.c.id), func.avg(jobs.c.queue_wait_ms),
                func.avg(jobs.c.execution_ms),
            ).where(
                jobs.c.owner == owner, jobs.c.status == "succeeded",
                jobs.c.execution_ms.is_not(None),
            )).one()
            sample = conn.execute(select(jobs.c.queue_wait_ms, jobs.c.execution_ms).where(
                jobs.c.owner == owner, jobs.c.status == "succeeded",
                jobs.c.execution_ms.is_not(None),
            ).order_by(jobs.c.updated_at.desc()).limit(1000)).all()
            retries = conn.scalar(select(func.coalesce(func.sum(
                case((jobs.c.attempts > 1, jobs.c.attempts - 1), else_=0)
            ), 0)).where(jobs.c.owner == owner))
            terminal = (counts.get("succeeded", 0) + counts.get("dead", 0))
            throughput = conn.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.owner == owner, jobs.c.status.in_(["succeeded", "dead"]),
                jobs.c.updated_at >= now - 300,
            ))
            failed_attempts = conn.scalar(select(func.count()).select_from(
                events.join(jobs, events.c.job_id == jobs.c.id)
            ).where(jobs.c.owner == owner, events.c.status.in_(["retry", "dead"])))

        def percentile(values, fraction):
            ordered = sorted(float(value) for value in values if value is not None)
            if not ordered:
                return 0.0
            return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

        queue_samples = [row[0] for row in sample]
        execution_samples = [row[1] for row in sample]
        completed = int(aggregates[0] or 0)
        return {
            "tasks": counts,
            "queued_depth": counts.get("queued", 0) + counts.get("retry", 0),
            "oldest_queue_seconds": max(0.0, now - oldest) if oldest is not None else 0.0,
            "failed_tasks_total": counts.get("dead", 0),
            "failed_attempts_total": int(failed_attempts or 0),
            "retries_total": int(retries or 0),
            "failure_rate_percent": round(counts.get("dead", 0) / terminal * 100, 2) if terminal else 0.0,
            "completed_last_5m": int(throughput or 0),
            "sampled_completed_tasks": completed,
            "avg_queue_wait_ms": round(float(aggregates[1] or 0), 2),
            "p95_queue_wait_ms": round(percentile(queue_samples, 0.95), 2),
            "avg_execution_ms": round(float(aggregates[2] or 0), 2),
            "p95_execution_ms": round(percentile(execution_samples, 0.95), 2),
            "percentile_sample_size": len(sample),
        }

    def progress(self, job, detail):
        now = time.time()
        with self.engine.begin() as conn:
            active = conn.scalar(select(jobs.c.id).where(
                jobs.c.id == job["id"], jobs.c.status == "running",
                jobs.c.lease_token == job["lease_token"], jobs.c.lease_until > now,
            ))
            if not active:
                return False
            self._event(conn, job["id"], "progress", detail[:500], now)
            return True


if __name__ == "__main__":
    from cloud.config import Settings
    store = Store(Settings.from_env().database_url)
    store.migrate()
    store.engine.dispose()
    print("Database schema v1 ready")
