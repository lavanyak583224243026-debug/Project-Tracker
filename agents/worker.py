"""Worker Agent: Project Execution and Tracking Specialist.

Responsible for executing approved actions using real Python tools and SQLite:
creating projects, saving milestones/tasks, updating statuses, calculating progress,
detecting overdue tasks, recommending next tasks, and exporting reports.
"""
import logging
from typing import Any, Dict, List, Optional, Union

from database.db import (
    create_project,
    get_project,
    get_tasks,
    update_task_status,
)
from services.gemini_service import get_crewai_llm
from tools.project_tools import (
    calculate_progress,
    create_project_tool,
    detect_overdue_tasks,
    export_csv_report,
    export_txt_report,
    get_project_summary,
    recommend_next_task,
    save_approved_plan,
    update_task_status_tool,
)

logger = logging.getLogger("worker_agent")

WORKER_ROLE = "Project Execution and Tracking Specialist"
WORKER_GOAL = (
    "Execute approved project plans in SQLite, maintain accurate task tracking, "
    "compute deterministic metrics, detect bottlenecks, and generate comprehensive reports."
)
WORKER_BACKSTORY = (
    "You are a rigorous technical project manager and software executor. You operate "
    "directly against SQLite databases and Python calculations to ensure projects remain "
    "on schedule, dependencies are respected, and data integrity is preserved."
)


def create_worker_agent(custom_tools: Optional[list] = None):
    """Create and return the CrewAI Worker Agent instance."""
    from crewai import Agent
    from tools.project_tools import get_crewai_worker_tools

    llm = get_crewai_llm()
    tools = custom_tools if custom_tools is not None else get_crewai_worker_tools()
    return Agent(
        role=WORKER_ROLE,
        goal=WORKER_GOAL,
        backstory=WORKER_BACKSTORY,
        llm=llm,
        tools=tools,
        verbose=True,
        allow_delegation=False,
    )


class WorkerExecutor:
    """Deterministic worker execution engine powered by Python tools and SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    def init_project(self, title: str, description: str, start_date: str, deadline: str) -> Dict[str, Any]:
        """Create project record in database."""
        return create_project_tool(title, description, start_date, deadline, db_path=self.db_path)

    def execute_plan(self, project_id: int, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Persist approved plan (milestones and tasks) to SQLite."""
        return save_approved_plan(project_id, plan, db_path=self.db_path)

    def update_status(self, task_id: int, status: str) -> Dict[str, Any]:
        """Update a task's status."""
        return update_task_status_tool(task_id, status, db_path=self.db_path)

    def compute_metrics(self, project_id: int) -> Dict[str, Any]:
        """Compute progress, overdue alerts, and next task recommendations using Python."""
        progress = calculate_progress(project_id, db_path=self.db_path)
        overdue = detect_overdue_tasks(project_id, db_path=self.db_path)
        recommendation = recommend_next_task(project_id, db_path=self.db_path)
        return {
            "progress": progress,
            "overdue_tasks": overdue,
            "next_task_recommendation": recommendation,
        }

    def generate_reports(self, project_id: int) -> Dict[str, str]:
        """Export CSV and TXT project reports."""
        csv_file = export_csv_report(project_id, db_path=self.db_path)
        txt_file = export_txt_report(project_id, db_path=self.db_path)
        return {
            "csv_path": csv_file,
            "txt_path": txt_file,
        }
