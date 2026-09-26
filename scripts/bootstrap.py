"""Create a private .env.cloud without changing an existing configuration."""
import json
import secrets
from pathlib import Path

target = Path(__file__).resolve().parents[1] / ".env.cloud"
content = (
    "DATABASE_URL=sqlite:///./cloud.db\n"
    + "API_KEYS=" + json.dumps({"demo": secrets.token_urlsafe(32)}) + "\n"
    + "POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n"
    + "AGENT_MODE=mock\nGEMINI_API_KEY=\nGEMINI_MODEL=gemini-2.5-flash\n"
)
try:
    with target.open("x", encoding="utf-8") as stream:
        stream.write(content)
    target.chmod(0o600)
except FileExistsError:
    print(".env.cloud already exists; left unchanged.")
else:
    print("Created .env.cloud. Use the demo API key there to connect to the dashboard.")
