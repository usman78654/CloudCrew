"""Cloud-runtime adapters for the repository's educational agents."""
import inspect
import json
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


class AssignmentSuccessInput(CoachInput):
    """One request shared by all stages in the Assignment Success Team."""
    student_draft: str = Field(default="", max_length=12000)
    citation_style: str = Field(default="APA", pattern="^(APA|MLA|Chicago|Harvard)$")


async def gemini(payload):
    from agents.gemini_wrapper_agent.client import call_gemini_or_mock
    result = await call_gemini_or_mock(payload["request"])
    if "error" in result:
        raise RuntimeError("Gemini provider request failed")
    return result


async def coach(payload, **_):
    # Reuse the original LangGraph planning workflow; persistence belongs to the runtime.
    from agents.assignment_coach.coach_agent import process_assignment_request
    coach_payload = {key: payload[key] for key in CoachInput.model_fields if key in payload}
    result = await process_assignment_request({"payload": coach_payload})
    if "error" in result:
        raise RuntimeError("Assignment Coach could not process the supplied assignment")
    from shared.gemini_http import cloud_enabled
    return {"output": json.loads(result["output"]), "mock": not cloud_enabled()}


def _dependency(context, agent):
    value = (context or {}).get(agent, {})
    return value.get("result") or {}


async def _role_response(role, instruction, payload, context, mock_result):
    """Use Gemini in cloud mode and explicit deterministic evidence in mock mode."""
    from shared.gemini_http import cloud_enabled
    if not cloud_enabled():
        return {"agent": role, "mode": "mock", **mock_result}
    prompt = json.dumps({
        "role": role,
        "instruction": instruction,
        "assignment": payload,
        "completed_stages": context or {},
        "response_requirement": "Return concise JSON. Do not invent sources or claim external checks you did not perform.",
    }, ensure_ascii=True)[:7900]
    response = await gemini({"request": prompt})
    return {"agent": role, "mode": "cloud", "output": response["output"]}


async def research_scout(payload, context=None, **_):
    coach_result = _dependency(context, "assignment-coach")
    topic = payload.get("subject") or payload["assignment_title"]
    return await _role_response(
        "research-scout",
        "Create a research strategy and source-selection criteria based on the Coach plan.",
        payload, context,
        {
            "topic": topic,
            "research_queries": [
                f'"{topic}" peer reviewed research',
                f'"{payload["assignment_title"]}" case study',
                f'"{topic}" systematic review',
            ],
            "source_requirements": [
                "Prefer peer-reviewed work and primary sources",
                "Verify author, publication, year, DOI, and relevance before citing",
                "Treat these as search prompts; mock mode does not claim to retrieve papers",
            ],
            "coach_plan_received": bool(coach_result),
        },
    )


async def study_scheduler(payload, context=None, **_):
    coach_result = _dependency(context, "assignment-coach")
    response = coach_result.get("output", {}).get("response", {})
    steps = response.get("task_plan", [])
    schedule = [
        {"session": index + 1,
         "task": step.get("step", step.get("description", "Work on assignment")),
         "estimate": step.get("estimated_time", "Set after reviewing scope")}
        for index, step in enumerate(steps)
    ]
    if not schedule:
        schedule = [{"session": 1, "task": "Review the assignment brief", "estimate": "30 minutes"}]
    return await _role_response(
        "study-scheduler",
        "Convert the Coach plan into a realistic schedule that respects the deadline.",
        payload, context,
        {"deadline": payload.get("deadline") or "Not supplied", "schedule": schedule,
         "coach_plan_received": bool(coach_result)},
    )


async def citation_manager(payload, context=None, **_):
    research = _dependency(context, "research-scout")
    return await _role_response(
        "citation-manager",
        "Create a citation checklist from the Research Scout output. Format only verifiable source metadata.",
        payload, context,
        {
            "style": payload.get("citation_style", "APA"),
            "research_handoff_received": bool(research),
            "checklist": [
                "Confirm every source exists before adding it to the bibliography",
                "Capture authors, title, publisher or journal, year, pages, DOI or URL",
                f'Apply {payload.get("citation_style", "APA")} consistently to citations',
            ],
            "citations": [],
            "note": "Mock mode does not fabricate bibliographic records.",
        },
    )


async def originality_check(payload, context=None, **_):
    draft = payload.get("student_draft", "").strip()
    if draft:
        words = draft.split()
        repeated = len(words) - len({word.lower().strip(".,:;!?") for word in words})
        mock = {
            "draft_received": True,
            "word_count": len(words),
            "repeated_word_count": repeated,
            "checks": ["Add citations for borrowed claims", "Use quotation marks for exact wording",
                       "Compare the final draft with the institution's approved similarity service"],
            "note": "This local preflight is not a database-backed plagiarism verdict.",
        }
    else:
        mock = {"draft_received": False, "word_count": 0,
                "checks": ["Run the originality stage again after a draft is available"],
                "note": "No student draft was supplied, so no text analysis was performed."}
    return await _role_response(
        "originality-check",
        "Perform an originality preflight. Never claim a database comparison unless one occurred.",
        payload, context, mock,
    )


async def assignment_success_team(payload, context=None, progress=None):
    """Synthesize the five durable upstream stages into one inspectable result."""
    async def announce(message):
        if progress is not None:
            outcome = progress(message)
            if inspect.isawaitable(outcome):
                await outcome

    await announce("Gemini Reviewer received all five educational-agent handoffs")
    upstream = {name: (item.get("result") or {}) for name, item in (context or {}).items()}
    review = await _role_response(
        "gemini-reviewer",
        "Synthesize the Coach, Research, Schedule, Citation, and Originality outputs into a final student action plan.",
        payload, context,
        {
            "summary": f'Assignment support package ready for {payload["assignment_title"]}.',
            "completed_handoffs": sorted(upstream),
            "next_actions": ["Follow the Coach plan and schedule",
                             "Verify sources before using the Citation Manager checklist",
                             "Revise the draft and repeat the originality preflight"],
        },
    )
    await announce("Gemini Reviewer produced the final assignment support package")
    return {
        "workflow": "assignment-success-team",
        "mode": review["mode"],
        "agents": ["assignment-coach", "research-scout", "study-scheduler",
                   "citation-manager", "originality-check", "gemini-reviewer"],
        "handoffs": upstream,
        "final_review": review,
    }


async def assignment_review_team(payload, progress=None, **_):
    """Backward-compatible two-agent Coach-to-Reviewer workflow."""
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
        review = await gemini({"request": json.dumps({
            "task": "Review this assignment plan for coverage, estimates, and actionable improvements.",
            "assignment_title": payload["assignment_title"],
            "plan": plan["output"]["response"],
        }, ensure_ascii=True)[:7900]})
        review_text, mode = review["output"], "cloud"
    else:
        estimates = [item.get("estimated_time", "unspecified") for item in steps]
        review_text = (f"Mock reviewer inspected {len(steps)} Coach steps with estimates "
                       f"{', '.join(estimates) or 'unspecified'}.")
        mode = "mock"
    await announce("Gemini Reviewer reviewed the Assignment Coach draft")
    return {"workflow": "assignment-review-team", "mode": mode,
            "agents": ["assignment-coach", "gemini-wrapper"],
            "plan": plan["output"], "review": review_text}


@dataclass(frozen=True)
class Plugin:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[..., Awaitable[dict]]


PLUGINS = {
    "gemini-wrapper": Plugin("Gemini Wrapper", "Text generation in mock or cloud mode", GeminiInput, gemini),
    "assignment-coach": Plugin("Assignment Coach", "Breaks an assignment into an actionable plan", CoachInput, coach),
    "research-scout": Plugin("Research Scout", "Builds a source discovery and verification strategy", AssignmentSuccessInput, research_scout),
    "study-scheduler": Plugin("Study Scheduler", "Turns the Coach plan into scheduled sessions", AssignmentSuccessInput, study_scheduler),
    "citation-manager": Plugin("Citation Manager", "Builds a citation checklist from research output", AssignmentSuccessInput, citation_manager),
    "originality-check": Plugin("Originality Preflight", "Reviews a supplied draft without overstating plagiarism detection", AssignmentSuccessInput, originality_check),
    "assignment-success-team": Plugin("Assignment Success Team", "Six educational agents collaborate through durable queued stages", AssignmentSuccessInput, assignment_success_team),
    "assignment-review-team": Plugin("Assignment Review Team", "Assignment Coach drafts a plan and Gemini reviews it", CoachInput, assignment_review_team),
}


def route(request):
    words = set(request.lower().replace("-", " ").split())
    if words & {"assignment", "homework", "coursework", "essay"}:
        return "assignment-coach", {"assignment_title": request[:200], "assignment_description": request}
    return "gemini-wrapper", {"request": request}
