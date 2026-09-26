import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, TypedDict
from langgraph.graph import StateGraph, END
from shared.gemini_http import cloud_enabled, generate_text

_logger = logging.getLogger(__name__)

def _configure_gemini():
    return cloud_enabled()

class AssignmentState(TypedDict):
    """State for the assignment coach agent"""
    input_data: Dict[str, Any]
    assignment_summary: str
    task_plan: list
    resources: list
    feedback: str
    motivation: str
    error: str

# Node functions for LangGraph
async def parse_input(state: AssignmentState) -> AssignmentState:
    """Parse and validate input data"""
    try:
        input_data = state["input_data"]
        payload = input_data.get("payload", {})
        
        if not payload.get("assignment_title"):
            state["error"] = "Missing assignment_title in payload"
        
        _logger.info("Parsed assignment input")
        return state
    except Exception as e:
        state["error"] = f"Parse error: {str(e)}"
        return state

async def generate_summary(state: AssignmentState) -> AssignmentState:
    """Generate assignment summary using Gemini API"""
    try:
        payload = state["input_data"].get("payload", {})
        title = payload.get("assignment_title", "")
        description = payload.get("assignment_description", "")
        subject = payload.get("subject", "")
        difficulty = payload.get("difficulty", "Intermediate")
        
        if _configure_gemini():
            try:
                prompt = f"""Create a concise 2-3 sentence summary for this assignment:
Title: {title}
Description: {description}
Subject: {subject}
Difficulty: {difficulty}

Focus on the key learning objectives and core components the student needs to understand."""
                
                response_text = await generate_text(prompt)
                state["assignment_summary"] = response_text.strip()
            except Exception as e:
                if os.getenv("AGENT_MODE") == "cloud":
                    raise
                _logger.warning(f"Gemini API failed: {e}, using fallback")
                state["assignment_summary"] = f"This {difficulty.lower()} level assignment on {subject} focuses on {title}. {description}"
        else:
            state["assignment_summary"] = f"This {difficulty.lower()} level assignment on {subject} focuses on {title}. {description}"
        
        _logger.info("Generated assignment summary")
        return state
    except Exception as e:
        state["error"] = f"Summary error: {str(e)}"
        return state

async def create_task_plan(state: AssignmentState) -> AssignmentState:
    """Create a task breakdown plan using Gemini API"""
    try:
        payload = state["input_data"].get("payload", {})
        title = payload.get("assignment_title", "")
        description = payload.get("assignment_description", "")
        subject = payload.get("subject", "")
        difficulty = payload.get("difficulty", "Intermediate")
        deadline = payload.get("deadline", "")
        student_profile = payload.get("student_profile", {})
        skills = student_profile.get("skills", [])
        
        if _configure_gemini():
            try:
                prompt = f"""Create a detailed 4-step task plan for this assignment:
Title: {title}
Description: {description}
Subject: {subject}
Difficulty: {difficulty}
Deadline: {deadline}
Student Skills: {', '.join(skills) if skills else 'General'}

Generate EXACTLY 4 tasks with realistic time estimates. Format as JSON array:
[
  {{"step": 1, "task": "specific task name", "estimated_time": "X days"}},
  {{"step": 2, "task": "specific task name", "estimated_time": "X days"}},
  {{"step": 3, "task": "specific task name", "estimated_time": "X days"}},
  {{"step": 4, "task": "specific task name", "estimated_time": "X days"}}
]

Make tasks specific to the assignment, not generic. Consider the difficulty level."""
                
                response_text = await generate_text(prompt)
                text = response_text.strip()
                
                # Extract JSON from response
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                tasks = json.loads(text)
                state["task_plan"] = tasks
            except Exception as e:
                if os.getenv("AGENT_MODE") == "cloud":
                    raise
                _logger.warning(f"Gemini task plan failed: {e}, using fallback")
                time_multiplier = {"Beginner": 1, "Intermediate": 1.5, "Advanced": 2}.get(difficulty, 1.5)
                state["task_plan"] = [
                    {"step": 1, "task": f"Research {subject} concepts and frameworks", "estimated_time": f"{int(2*time_multiplier)} days"},
                    {"step": 2, "task": f"Create outline and architecture diagram", "estimated_time": f"{int(1*time_multiplier)} days"},
                    {"step": 3, "task": f"Write first draft of the {title.lower()}", "estimated_time": f"{int(2*time_multiplier)} days"},
                    {"step": 4, "task": "Review, proofread, and finalize", "estimated_time": f"{int(1*time_multiplier)} days"}
                ]
        else:
            time_multiplier = {"Beginner": 1, "Intermediate": 1.5, "Advanced": 2}.get(difficulty, 1.5)
            state["task_plan"] = [
                {"step": 1, "task": f"Research {subject} concepts", "estimated_time": f"{int(2*time_multiplier)} days"},
                {"step": 2, "task": "Create structure", "estimated_time": f"{int(1*time_multiplier)} days"},
                {"step": 3, "task": "Draft content", "estimated_time": f"{int(2*time_multiplier)} days"},
                {"step": 4, "task": "Review and finalize", "estimated_time": f"{int(1*time_multiplier)} days"}
            ]
        
        _logger.info("Created task plan")
        return state
    except Exception as e:
        state["error"] = f"Task plan error: {str(e)}"
        return state

async def recommend_resources(state: AssignmentState) -> AssignmentState:
    """Recommend learning resources using Gemini API"""
    try:
        payload = state["input_data"].get("payload", {})
        title = payload.get("assignment_title", "")
        subject = payload.get("subject", "General")
        student_profile = payload.get("student_profile", {})
        learning_style = student_profile.get("learning_style", "mixed")
        
        if _configure_gemini():
            try:
                prompt = f"""Recommend 3 specific learning resources for this assignment:
Assignment: {title}
Subject: {subject}
Learning Style: {learning_style}

Generate EXACTLY 3 resources in JSON format:
[
  {{"type": "article", "title": "specific article/doc title", "url": "realistic url"}},
  {{"type": "video", "title": "specific video title", "url": "realistic youtube/platform url"}},
  {{"type": "tool", "title": "specific tool/framework", "url": "realistic tool url"}}
]

Prioritize {"videos and interactive content" if learning_style == "visual" else "articles and documentation" if learning_style == "reading" else "hands-on projects" if learning_style == "hands-on" else "mixed resources"}."""
                
                response_text = await generate_text(prompt)
                text = response_text.strip()
                
                # Extract JSON
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                resources = json.loads(text)
                state["resources"] = resources
            except Exception as e:
                if os.getenv("AGENT_MODE") == "cloud":
                    raise
                _logger.warning(f"Gemini resources failed: {e}, using fallback")
                resources = []
                if learning_style in ["visual", "mixed"]:
                    resources.append({
                        "type": "video",
                        "title": f"How {subject} Works - Simplified Explanation",
                        "url": f"https://www.youtube.com/results?search_query={subject.replace(' ', '+')}"
                    })
                resources.append({
                    "type": "article",
                    "title": f"{subject} - Official Documentation",
                    "url": f"https://scholar.google.com/scholar?q={subject.replace(' ', '+')}"
                })
                resources.append({
                    "type": "tool",
                    "title": "Assignment Planning Template",
                    "url": "https://docs.google.com/document"
                })
                state["resources"] = resources
        else:
            resources = []
            if learning_style in ["visual", "mixed"]:
                resources.append({
                    "type": "video",
                    "title": f"{subject} Tutorial",
                    "url": f"https://www.youtube.com/results?search_query={subject.replace(' ', '+')}"
                })
            resources.append({
                "type": "article",
                "title": f"{subject} Guide",
                "url": f"https://scholar.google.com/scholar?q={subject.replace(' ', '+')}"
            })
            resources.append({
                "type": "tool",
                "title": "Planning Tool",
                "url": "https://docs.google.com/document"
            })
            state["resources"] = resources
        
        _logger.info("Generated resource recommendations")
        return state
    except Exception as e:
        state["error"] = f"Resource error: {str(e)}"
        return state

async def generate_feedback(state: AssignmentState) -> AssignmentState:
    """Generate personalized feedback and motivation using Gemini API"""
    try:
        payload = state["input_data"].get("payload", {})
        title = payload.get("assignment_title", "")
        student_profile = payload.get("student_profile", {})
        progress = student_profile.get("progress", 0)
        skills = student_profile.get("skills", [])
        weaknesses = student_profile.get("weaknesses", [])
        deadline = payload.get("deadline", "")
        
        progress_pct = int(progress * 100)
        
        if _configure_gemini():
            try:
                prompt = f"""Generate personalized feedback and motivation for a student:
Assignment: {title}
Progress: {progress_pct}%
Deadline: {deadline}
Strengths: {', '.join(skills) if skills else 'None listed'}
Weaknesses: {', '.join(weaknesses) if weaknesses else 'None listed'}

Provide:
1. FEEDBACK: One clear sentence about their progress and what to focus on next
2. MOTIVATION: One actionable tip addressing their weaknesses

Format as JSON:
{{
  "feedback": "progress update and next action",
  "motivation": "specific tip for their weakness"
}}"""
                
                response_text = await generate_text(prompt)
                text = response_text.strip()
                
                # Extract JSON
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                result = json.loads(text)
                state["feedback"] = result.get("feedback", f"You have completed {progress_pct}% of your work.")
                state["motivation"] = result.get("motivation", "Stay focused and break tasks into manageable chunks!")
            except Exception as e:
                if os.getenv("AGENT_MODE") == "cloud":
                    raise
                _logger.warning(f"Gemini feedback failed: {e}, using fallback")
                state["feedback"] = f"You have completed {progress_pct}% of your work. "
                
                if progress < 0.3:
                    state["feedback"] += "Try finalizing your research phase to stay on track."
                elif progress < 0.7:
                    state["feedback"] += "Good progress! Focus on drafting the main content."
                else:
                    state["feedback"] += "Almost there! Complete your final review."
                
                if weaknesses:
                    if "time management" in weaknesses:
                        state["motivation"] = "Break tasks into short sessions and take small breaks for better focus."
                    elif "writing" in weaknesses or "coding" in weaknesses:
                        state["motivation"] = "Start with a rough draft and improve gradually. Perfection comes with iteration!"
                    else:
                        state["motivation"] = f"Focus on improving your {weaknesses[0]} skills through practice and review."
                else:
                    state["motivation"] = "You're progressing well! Maintain consistency and stay organized."
        else:
            state["feedback"] = f"You have completed {progress_pct}% of your work. "
            if progress < 0.3:
                state["feedback"] += "Start strong with research."
            elif progress < 0.7:
                state["feedback"] += "Keep going with your draft."
            else:
                state["feedback"] += "Finish with a thorough review."
            
            state["motivation"] = "Stay focused and organized. You can do this!"
        
        _logger.info("Generated feedback and motivation")
        return state
    except Exception as e:
        state["error"] = f"Feedback error: {str(e)}"
        return state

def should_continue(state: AssignmentState) -> str:
    """Decide whether to continue or end"""
    if state.get("error"):
        return "end"
    return "continue"

# Build LangGraph workflow
def create_workflow():
    """Create the LangGraph workflow"""
    workflow = StateGraph(AssignmentState)
    
    # Add nodes
    workflow.add_node("parse", parse_input)
    workflow.add_node("summary", generate_summary)
    workflow.add_node("tasks", create_task_plan)
    workflow.add_node("resources", recommend_resources)
    workflow.add_node("feedback", generate_feedback)
    
    # Define edges
    workflow.set_entry_point("parse")
    workflow.add_conditional_edges("parse", should_continue, {"continue": "summary", "end": END})
    workflow.add_conditional_edges("summary", should_continue, {"continue": "tasks", "end": END})
    workflow.add_conditional_edges("tasks", should_continue, {"continue": "resources", "end": END})
    workflow.add_conditional_edges("resources", should_continue, {"continue": "feedback", "end": END})
    workflow.add_edge("feedback", END)
    
    return workflow.compile()

# Initialize workflow
graph = create_workflow()

async def process_assignment_request(input_request: str) -> Dict[str, Any]:
    """Main processing function using LangGraph"""
    try:
        # Parse input
        if isinstance(input_request, str):
            try:
                input_data = json.loads(input_request)
            except:
                input_data = {"payload": {"assignment_title": input_request}}
        else:
            input_data = input_request
        
        # Initialize state
        initial_state: AssignmentState = {
            "input_data": input_data,
            "assignment_summary": "",
            "task_plan": [],
            "resources": [],
            "feedback": "",
            "motivation": "",
            "error": ""
        }
        
        # Run the graph
        final_state = await graph.ainvoke(initial_state)
        
        # Check for errors
        if final_state.get("error"):
            return {"error": final_state["error"]}
        
        # Build output in the required format
        output = {
            "agent_name": input_data.get("agent_name", "assignment_coach_agent"),
            "status": "success",
            "response": {
                "assignment_summary": final_state["assignment_summary"],
                "task_plan": final_state["task_plan"],
                "recommended_resources": final_state["resources"],
                "feedback": final_state["feedback"],
                "motivation": final_state["motivation"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
        
        return {"output": json.dumps(output), "cached": False}
        
    except Exception as e:
        _logger.error(f"Error processing assignment request: {e}")
        return {"error": f"Processing failed: {str(e)}"}
