"""Small async REST client shared by the two showcased agents."""
import asyncio
import os
import re

import httpx


def cloud_enabled():
    mode = os.getenv("AGENT_MODE", "auto")
    if mode not in {"auto", "mock", "cloud"}:
        raise ValueError("AGENT_MODE must be auto, mock or cloud")
    if mode == "mock":
        return False
    if mode == "cloud" and not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("Cloud mode requires GEMINI_API_KEY")
    return mode == "cloud" or bool(os.getenv("GEMINI_API_KEY"))


async def mock_delay():
    """Optional, bounded latency for local load and worker-recovery demonstrations."""
    delay_ms = int(os.getenv("MOCK_LATENCY_MS", "0"))
    if not 0 <= delay_ms <= 30_000:
        raise ValueError("MOCK_LATENCY_MS must be between 0 and 30000")
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)


async def generate_text(prompt, model=None):
    model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
        raise ValueError("Invalid Gemini model identifier")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={"contents": [{"parts": [{"text": prompt}]}]},
        )
        # Avoid exceptions containing provider response bodies or API keys.
        if response.status_code != 200:
            raise RuntimeError(f"Gemini returned HTTP {response.status_code}")
        data = response.json()
    candidates = data.get("candidates", [])
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    output = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    if not output:
        raise RuntimeError("Gemini returned no text")
    return output
