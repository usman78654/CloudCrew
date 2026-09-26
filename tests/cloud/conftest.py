import os
import uuid

import pytest

from cloud.store import Store


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
    yield db
    db.engine.dispose()


@pytest.fixture
def owner():
    return "test-" + uuid.uuid4().hex
