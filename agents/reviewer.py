"""Reviewer Agent: Quality Assurance and Validation Specialist.

Responsible for reviewing proposed plans and execution results, verifying
computations against SQLite, catching date/dependency violations, and providing
actionable feedback routed to Planner or Worker within a 2-revision limit.
"""
import json
import logging
from typing import Any, Dict, Optional, Tuple

from database.db import log_review
from services.gemini_service import (
    clean_json_response,
    generate_gemini_content,
    get_crewai_llm,
)
from tools.project_tools import review_results, validate_plan

logger = logging.getLogger("reviewer_agent")

REVIEWER_ROLE = "Quality Assurance and Validation Specialist"
REVIEWER_GOAL = (
    "Rigorously audit project plans and execution outputs for structural completeness, "
    "date validity, dependency integrity, and mathematical alignment with SQLite records."
)
REVIEWER_BACKSTORY = (
    "You are a strict academic QA examiner and technical reviewer. You inspect "
    "student project plans with zero tolerance for date inconsistencies, missing milestones, "
    "circular dependencies, or calculation discrepancies. You give targeted, actionable feedback."
)


def create_reviewer_agent(custom_tools: Optional[list] = None):
    """Create and return the CrewAI Reviewer Agent instance."""
    from crewai import Agent
    from tools.project_tools import get_crewai_reviewer_tools

    llm = get_crewai_llm()
    tools = custom_tools if custom_tools is not None else get_crewai_reviewer_tools()
    return Agent(
        role=REVIEWER_ROLE,
        goal=REVIEWER_GOAL,
        backstory=REVIEWER_BACKSTORY,
        llm=llm,
        tools=tools,
        verbose=True,
        allow_delegation=False,
    )


def review_plan(
    plan: Dict[str, Any],
    project_start_date: str,
    project_deadline: str,
    attempt: int = 1,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """Audit proposed plan using Python rules first, followed by AI review if requested."""
    # 1. Deterministic Python validation (hard ground truth)
    val = validate_plan(plan, project_start_date, project_deadline)

    if not val["valid"]:
        feedback = "Plan validation failed: " + "; ".join(val["errors"])
        return {
            "decision": "Rejected",
            "target_agent": "Planner",
            "attempt": attempt,
            "feedback": feedback,
            "errors": val["errors"],
            "warnings": val["warnings"],
            "cleaned_plan": None,
        }

    # 2. If AI review is enabled and available, run semantic QA check
    ai_feedback = "Plan passed all deterministic integrity checks."
    if use_ai:
        try:
            prompt = f"""
You are the Quality Assurance and Validation Specialist. Audit this proposed student project plan.

TIMELINE:
- Start: {project_start_date}
- Deadline: {project_deadline}

PROPOSED PLAN:
{json.dumps(val['cleaned_plan'], indent=2)}

AUDIT CRITERIA:
1. Are milestones logical and sequential?
2. Are tasks concrete and achievable for university final-year projects?
3. Are dependencies realistic?
4. Are deadlines spread logically across the duration?

Respond ONLY in JSON:
{{
  "approved": true,
  "feedback": "Detailed audit remarks",
  "recommendations": ["suggestion 1"]
}}
"""
            raw_response = generate_gemini_content(
                prompt=prompt,
                system_instruction="You are a strict QA auditor. Return only valid JSON.",
            )
            parsed = json.loads(clean_json_response(raw_response))
            if not parsed.get("approved", True):
                return {
                    "decision": "Rejected",
                    "target_agent": "Planner",
                    "attempt": attempt,
                    "feedback": parsed.get("feedback", "AI Reviewer found structural flaws in the plan."),
                    "errors": [parsed.get("feedback")],
                    "warnings": val["warnings"],
                    "cleaned_plan": None,
                }
            ai_feedback = parsed.get("feedback", ai_feedback)
        except Exception as ex:
            # If Gemini fails, do NOT fail the review if Python rules passed,
            # but note that AI QA was bypassed/unavailable.
            ai_feedback += f" (Note: AI semantic audit skipped: {type(ex).__name__})"

    return {
        "decision": "Approved",
        "target_agent": "None",
        "attempt": attempt,
        "feedback": ai_feedback,
        "errors": [],
        "warnings": val["warnings"],
        "cleaned_plan": val["cleaned_plan"],
    }


def review_execution(
    project_id: int,
    claimed_progress: Optional[float] = None,
    attempt: int = 1,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify execution results against actual SQLite data."""
    result = review_results(
        project_id=project_id,
        action_type="execution_validation",
        result_data={"claimed_progress": claimed_progress},
        db_path=db_path,
    )

    decision = result["decision"]
    feedback = result["feedback"]
    target_agent = result["target_agent"]

    # Log in review_logs table
    log_review(
        project_id=project_id,
        attempt=attempt,
        decision=decision,
        feedback=feedback,
        db_path=db_path,
    )

    return {
        "decision": decision,
        "target_agent": target_agent,
        "attempt": attempt,
        "feedback": feedback,
        "verified": result["verified"],
    }
