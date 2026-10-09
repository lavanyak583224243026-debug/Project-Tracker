"""Planner Agent: Project Planning Specialist.

Responsible for analyzing student project requirements and generating a structured
JSON plan with milestones, tasks, priorities, deadlines, and dependencies.
"""
import json
import logging
from typing import Any, Dict, Optional

from services.gemini_service import clean_json_response, generate_gemini_content, get_crewai_llm
from tools.project_tools import validate_plan

logger = logging.getLogger("planner_agent")

PLANNER_ROLE = "Project Planning Specialist"
PLANNER_GOAL = (
    "Deconstruct student final-year project requirements into structured milestones "
    "and actionable tasks with strict date bounds, priorities, and dependencies."
)
PLANNER_BACKSTORY = (
    "You are an expert academic project advisor and technical lead. You specialize "
    "in turning complex engineering requirements into clear, step-by-step milestones "
    "and tasks that students can execute realistically within their semester deadlines."
)


def create_planner_agent():
    """Create and return the CrewAI Planner Agent instance."""
    from crewai import Agent

    llm = get_crewai_llm()
    return Agent(
        role=PLANNER_ROLE,
        goal=PLANNER_GOAL,
        backstory=PLANNER_BACKSTORY,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def plan_project(
    title: str,
    description: str,
    start_date: str,
    deadline: str,
    requirements_text: Optional[str] = None,
    reviewer_feedback: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate or revise a structured project plan.

    Calls Google Gemini using the Planner persona and enforces JSON output validation.
    """
    feedback_section = ""
    if reviewer_feedback:
        feedback_section = f"""
IMPORTANT - PREVIOUS REVIEW REJECTION FEEDBACK TO FIX:
The Reviewer Agent rejected the previous plan attempt with this specific feedback:
"{reviewer_feedback}"
You MUST correct all reported errors (dates, dependencies, missing fields).
"""

    req_section = ""
    if requirements_text and requirements_text.strip():
        req_section = f"""
SUPPLEMENTARY REQUIREMENTS DOCUMENT CONTENT:
\"\"\"
{requirements_text[:3000]}
\"\"\"
"""

    prompt = f"""
You are the Project Planning Specialist. Analyze the final-year project information below and decompose it into realistic milestones and tasks.

PROJECT DETAILS:
- Title: {title}
- Description: {description}
- Start Date: {start_date} (YYYY-MM-DD)
- Deadline: {deadline} (YYYY-MM-DD)
{req_section}
{feedback_section}

STRICT TIMELINE & DEPENDENCY RULES:
1. Every milestone and task due_date MUST be formatted as YYYY-MM-DD.
2. Every due_date MUST fall between {start_date} and {deadline} inclusive.
3. Every task MUST belong to a milestone (via milestone_title).
4. Task priorities must be exactly one of: 'Critical', 'High', 'Medium', 'Low'.
5. Dependencies must list preceding task IDs or titles. Do NOT include circular dependencies or self-dependencies.
6. Return ONLY valid, raw JSON matching the schema below. No commentary, no conversational text.

REQUIRED JSON SCHEMA:
{{
  "milestones": [
    {{
      "title": "Milestone 1: Requirement Analysis & Design",
      "description": "System architecture and specifications",
      "due_date": "YYYY-MM-DD"
    }}
  ],
  "tasks": [
    {{
      "id": 1,
      "milestone_title": "Milestone 1: Requirement Analysis & Design",
      "title": "Literature Review & Problem Formulation",
      "description": "Examine state of the art and write problem statement",
      "priority": "High",
      "due_date": "YYYY-MM-DD",
      "dependencies": []
    }},
    {{
      "id": 2,
      "milestone_title": "Milestone 1: Requirement Analysis & Design",
      "title": "System Architecture & Database Design",
      "description": "Draft schema and architectural diagram",
      "priority": "High",
      "due_date": "YYYY-MM-DD",
      "dependencies": [1]
    }}
  ]
}}
"""

    raw_response = generate_gemini_content(
        prompt=prompt,
        system_instruction=(
            "You are a structured planning agent. Output strictly valid JSON with no markdown tags or conversational filler."
        ),
    )

    cleaned = clean_json_response(raw_response)
    try:
        plan_data = json.loads(cleaned)
    except json.JSONDecodeError as ex:
        # Fallback to cleaning regex
        import re
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            plan_data = json.loads(match.group(0))
        else:
            raise ValueError(f"Planner response could not be parsed as JSON: {str(ex)}\nRaw: {raw_response[:200]}")

    # Validate output plan
    validation = validate_plan(plan_data, start_date, deadline)
    return {
        "raw_plan": plan_data,
        "validation": validation,
        "is_valid": validation["valid"],
    }
