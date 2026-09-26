"""Explicit plugin registry: handlers are async callables accepting JSON dictionaries."""
import json
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class GeminiInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=8000)


class CoachInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_title: str = Field(min_length=1, max_length=200)
    assignment_description: str = Field(default="", max_length=8000)
    subject: str = Field(default="General", max_length=200)
    deadline: str = Field(default="", max_length=50)


async def gemini(payload):
    from agents.gemini_wrapper_agent.client import call_gemini_or_mock
    result = await call_gemini_or_mock(payload["request"])
    if "error" in result:
        raise RuntimeError("Gemini provider request failed")
    return result


async def coach(payload):
    # Reuse the original LangGraph planning workflow; persistence belongs to the runtime.
    from agents.assignment_coach.coach_agent import process_assignment_request
    result = await process_assignment_request({"payload": payload})
    if "error" in result:
        raise RuntimeError("Assignment Coach could not process the supplied assignment")
    from shared.gemini_http import cloud_enabled
    return {"output": json.loads(result["output"]), "mock": not cloud_enabled()}


async def assignment_review_team(payload, progress=None):
    """Run Assignment Coach, then give its actual plan to an independent reviewer."""
    from shared.gemini_http import cloud_enabled

    async def announce(message):
        if progress is not None:
            outcome = progress(message)
            if inspect.isawaitable(outcome):
                await outcome

    await announce("Assignment Coach is drafting the plan")
    plan = await coach(payload)
    steps = plan["output"]["response"].get("task_plan", [])
    await announce(f"Assignment Coach finished its draft ({len(steps)} steps)")

    if cloud_enabled():
        # The reviewer receives the first agent's structured result, not another copy
        # of the user's original request. Bound the model input to the Gemini schema.
        review = await gemini({"request": json.dumps({
            "task": "Review another agent's assignment plan for coverage, realistic estimates, and actionable improvements. Return a concise review.",
            "assignment_title": payload["assignment_title"],
            "subject": payload["subject"],
            "plan": plan["output"]["response"],
        }, ensure_ascii=True)[:7900]})
        review_text = review["output"]
        mode = "cloud"
    else:
        # Make local collaboration real and inspectable without pretending mock output
        # is an LLM judgment: the reviewer reads and counts the actual coach plan.
        estimates = [item.get("estimated_time", "unspecified") for item in steps]
        review_text = (
            f"Mock reviewer inspected the Assignment Coach draft: {len(steps)} steps, "
            f"with estimates {', '.join(estimates) or 'unspecified'}. "
            "Check that the combined timeline fits the deadline and assignment rubric."
        )
        mode = "mock"

    await announce("Gemini Reviewer reviewed the Assignment Coach draft")
    return {
        "workflow": "assignment-review-team",
        "mode": mode,
        "agents": ["assignment-coach", "gemini-wrapper"],
        "plan": plan["output"],
        "review": review_text,
    }


@dataclass(frozen=True)
class Plugin:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[[dict], Awaitable[dict]]


PLUGINS = {
    "gemini-wrapper": Plugin("Gemini Wrapper", "Text generation in mock or cloud mode", GeminiInput, gemini),
    "assignment-coach": Plugin("Assignment Coach", "Plan, resources, feedback and review using LangGraph", CoachInput, coach),
    "assignment-review-team": Plugin(
        "Assignment Review Team",
        "Assignment Coach drafts a task plan and Gemini reviews that exact plan",
        CoachInput,
        assignment_review_team,
    ),
}


def route(request):
    words = set(request.lower().replace("-", " ").split())
    if words & {"assignment", "homework", "coursework", "essay"}:
        return "assignment-coach", {"assignment_title": request[:200], "assignment_description": request}
    return "gemini-wrapper", {"request": request}
