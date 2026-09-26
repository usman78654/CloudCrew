"""Gemini adapter with deterministic, explicit offline mode."""
from shared.gemini_http import cloud_enabled, generate_text, mock_delay


async def call_gemini_or_mock(input_text: str, model_override: str = None) -> dict:
    if not cloud_enabled():
        await mock_delay()
        return {"output": f"Mock response: {input_text}", "mock": True, "cached": False}
    output = await generate_text(input_text, model_override)
    return {"output": output, "mock": False, "cached": False}
