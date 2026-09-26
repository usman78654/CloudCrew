"""Configuration shared by API, migration command, and worker processes."""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./cloud.db"
    api_keys: dict[str, str] = field(default_factory=dict)
    mode: str = "mock"
    lease_seconds: int = 90
    task_timeout: int = 60
    poll_seconds: float = 1.0
    max_attempts: int = 3

    def __post_init__(self):
        if self.mode not in {"mock", "cloud"}:
            raise ValueError("AGENT_MODE must be mock or cloud")
        if not 0 < self.task_timeout < self.lease_seconds:
            raise ValueError("Task timeout must be positive and shorter than the lease")
        if self.poll_seconds <= 0 or not 1 <= self.max_attempts <= 10:
            raise ValueError("Invalid polling interval or attempt limit")
        if any(not owner or len(key) < 32 for owner, key in self.api_keys.items()):
            raise ValueError("API_KEYS must map nonempty owner names to keys of at least 32 characters")
        if len(set(self.api_keys.values())) != len(self.api_keys):
            raise ValueError("Each owner needs a different API key")

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env.cloud")
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./cloud.db"),
            api_keys=json.loads(os.getenv("API_KEYS", "{}")),
            mode=os.getenv("AGENT_MODE", "mock"),
            lease_seconds=int(os.getenv("LEASE_SECONDS", "90")),
            task_timeout=int(os.getenv("TASK_TIMEOUT", "60")),
            poll_seconds=float(os.getenv("POLL_SECONDS", "1")),
            max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
        )
