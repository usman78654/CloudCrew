import os
import uuid

import pytest
from sqlalchemy import delete, select

from cloud.store import Store, events, jobs, workers


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "postgres":
        url = os.getenv("TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL queue tests")
    else:
        url = "sqlite:///" + (tmp_path / "queue.db").as_posix()
    db = Store(url)
    db.migrate()
    if request.param == "postgres":
        # TEST_DATABASE_URL must point to an expendable database. Isolate each
        # parametrized case so workers cannot claim compatible jobs left queued
        # by an earlier test.
        with db.engine.begin() as conn:
            job_ids = select(jobs.c.id)
            conn.execute(delete(events).where(events.c.job_id.in_(job_ids)))
            conn.execute(delete(jobs))
            conn.execute(delete(workers))
    yield db
    db.engine.dispose()


@pytest.fixture
def owner():
    return "test-" + uuid.uuid4().hex
