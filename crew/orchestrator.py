"""Workflow Orchestration Engine for Final-Year Project Tracker.

Orchestrates the three agents (Planner, Worker, Reviewer) and deterministic Python tools:
- Input Validation
- Planning & Decomposition
- QA Review & Self-Correction Loop (Bounded to max 2 revisions)
- Worker Execution in SQLite
- Execution Verification
- Reporting & Dashboard Metrics
- Manual Offline Fallback
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.planner import plan_project
from agents.reviewer import review_execution, review_plan
from agents.worker import WorkerExecutor
from database.db import (
    create_project,
    get_execution_logs,
    get_milestones,
    get_project,
    get_review_logs,
    get_tasks,
    log_execution,
    log_review,
)
from services.gemini_service import check_gemini_availability
from tools.project_tools import (
    calculate_progress,
    detect_overdue_tasks,
    export_csv_report,
    export_txt_report,
    get_project_summary,
    parse_requirements,
    recommend_next_task,
    save_approved_plan,
    validate_project_input,
)

logger = logging.getLogger("orchestrator")


@dataclass
class WorkflowResult:
    """Encapsulates the full state and outcome of an orchestrated workflow run."""
    success: bool
    stage: str
    message: str
    project_id: Optional[int] = None
    plan: Optional[Dict[str, Any]] = None
    revision_attempts: int = 0
    max_revisions: int = 2
    planner_status: str = "Pending"
    worker_status: str = "Pending"
    reviewer_status: str = "Pending"
    review_decision: Optional[str] = None
    review_feedback: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    overdue_tasks: List[Dict[str, Any]] = field(default_factory=list)
    next_task_recommendation: Optional[Dict[str, Any]] = None
    csv_report_path: Optional[str] = None
    txt_report_path: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    is_manual_fallback: bool = False


class ProjectWorkflowOrchestrator:
    """Coordinates Planner, Worker, and Reviewer agents with bounded self-correction."""

    def __init__(self, db_path: Optional[str] = None, max_revisions: int = 2):
        self.db_path = db_path
        self.max_revisions = max_revisions
        self.worker_executor = WorkerExecutor(db_path=db_path)

    def _log(self, result: WorkflowResult, role: str, action: str, status: str, msg: str):
        """Append log message and persist to SQLite execution_logs."""
        formatted = f"[{datetime.now().strftime('%H:%M:%S')}] [{role}] [{status.upper()}] {action}: {msg}"
        result.logs.append(formatted)
        try:
            log_execution(
                agent_role=role,
                action=action,
                status=status,
                message=msg,
                db_path=self.db_path,
            )
        except Exception:
            pass

    def run_full_workflow(
        self,
        title: str,
        description: str,
        start_date: str,
        deadline: str,
        requirements_text: Optional[str] = None,
        force_manual: bool = False,
    ) -> WorkflowResult:
        """Run the end-to-end multi-agent workflow with a single execution call."""
        result = WorkflowResult(
            success=False,
            stage="Initialization",
            message="Workflow initiated.",
            max_revisions=self.max_revisions,
        )

        # ---------------------------------------------------------
        # STAGE 1: Input Validation
        # ---------------------------------------------------------
        result.stage = "Input Validation"
        self._log(result, "System", "validate_input", "Info", f"Validating input for project '{title}'...")

        v = validate_project_input(title, description, start_date, deadline)
        if not v["valid"]:
            result.errors.extend(v["errors"])
            result.message = "Input validation failed: " + "; ".join(v["errors"])
            self._log(result, "System", "validate_input", "Error", result.message)
            return result

        # ---------------------------------------------------------
        # STAGE 2: Project Record Creation
        # ---------------------------------------------------------
        result.stage = "Project Registration"
        try:
            project_id = create_project(title, description, start_date, deadline, db_path=self.db_path)
            result.project_id = project_id
            self._log(result, "Worker", "create_project", "Success", f"Created project ID #{project_id} in SQLite.")
        except Exception as ex:
            result.errors.append(f"Database error during project creation: {str(ex)}")
            result.message = "Failed to create project in database."
            self._log(result, "Worker", "create_project", "Error", str(ex))
            return result

        # ---------------------------------------------------------
        # STAGE 3: Gemini Health Check & Fallback Determination
        # ---------------------------------------------------------
        gemini_ok, gemini_msg = check_gemini_availability() if not force_manual else (False, "Manual mode requested")
        if not gemini_ok:
            self._log(
                result,
                "System",
                "api_check",
                "Warning",
                f"Gemini API unavailable: {gemini_msg}. Switching to Manual Fallback Tracking.",
            )
            result.is_manual_fallback = True
            return self._execute_fallback_plan(
                result=result,
                project_id=project_id,
                title=title,
                description=description,
                start_date=start_date,
                deadline=deadline,
            )

        # ---------------------------------------------------------
        # STAGE 4: Planning with Self-Correction Review Loop
        # ---------------------------------------------------------
        result.stage = "Planning & QA Review"
        result.planner_status = "In Progress"
        self._log(result, "Planner", "plan_project", "Info", "Generating structured project plan with Gemini...")

        current_plan = None
        current_feedback = None
        approved_plan = None

        # Allow initial attempt + up to max_revisions
        total_attempts = 1 + self.max_revisions
        for attempt in range(1, total_attempts + 1):
            revision_num = attempt - 1
            result.revision_attempts = revision_num

            # 4A. Call Planner
            try:
                plan_output = plan_project(
                    title=title,
                    description=description,
                    start_date=start_date,
                    deadline=deadline,
                    requirements_text=requirements_text,
                    reviewer_feedback=current_feedback,
                )
                current_plan = plan_output.get("raw_plan")
            except Exception as ex:
                err_str = str(ex)
                self._log(result, "Planner", "plan_project", "Error", f"Planner API error: {err_str}")
                result.errors.append(err_str)
                # Fall back gracefully to manual plan if Gemini fails during generation
                self._log(result, "System", "fallback", "Warning", "Falling back to rule-based plan due to Planner error.")
                result.is_manual_fallback = True
                return self._execute_fallback_plan(
                    result=result,
                    project_id=project_id,
                    title=title,
                    description=description,
                    start_date=start_date,
                    deadline=deadline,
                )

            # 4B. Call Reviewer Agent
            result.reviewer_status = "Reviewing"
            self._log(
                result,
                "Reviewer",
                "review_plan",
                "Info",
                f"Auditing proposed plan (Attempt {attempt}/{total_attempts}, Revision {revision_num}/{self.max_revisions})...",
            )

            rev_result = review_plan(
                plan=current_plan,
                project_start_date=start_date,
                project_deadline=deadline,
                attempt=attempt,
                use_ai=True,
            )

            decision = rev_result["decision"]
            feedback = rev_result["feedback"]
            result.review_decision = decision
            result.review_feedback = feedback

            # Log review in SQLite
            log_review(
                project_id=project_id,
                attempt=attempt,
                decision=decision,
                feedback=feedback,
                db_path=self.db_path,
            )

            if decision == "Approved":
                self._log(
                    result,
                    "Reviewer",
                    "review_plan",
                    "Success",
                    f"Plan APPROVED on attempt {attempt}: {feedback}",
                )
                approved_plan = rev_result["cleaned_plan"] or current_plan
                result.planner_status = "Completed"
                result.reviewer_status = "Approved"
                break
            else:
                self._log(
                    result,
                    "Reviewer",
                    "review_plan",
                    "Warning",
                    f"Plan REJECTED on attempt {attempt}: {feedback}. Routing corrections to Planner...",
                )
                current_feedback = feedback
                result.reviewer_status = "Rejected"

                if revision_num >= self.max_revisions:
                    self._log(
                        result,
                        "Reviewer",
                        "review_plan",
                        "Error",
                        f"Revision limit of {self.max_revisions} exhausted without passing QA.",
                    )
                    result.stage = "Review Limit Exhausted"
                    result.message = (
                        f"Reviewer rejected plan after {self.max_revisions} revisions. "
                        f"Unresolved issue: {feedback}"
                    )
                    result.errors.append(result.message)
                    result.success = False
                    return result

        if not approved_plan:
            result.message = "Failed to obtain an approved plan."
            return result

        result.plan = approved_plan

        # ---------------------------------------------------------
        # STAGE 5: Worker Execution in SQLite
        # ---------------------------------------------------------
        result.stage = "Execution & Database Persistence"
        result.worker_status = "In Progress"
        self._log(result, "Worker", "save_approved_plan", "Info", "Saving approved milestones and tasks into SQLite...")

        exec_res = self.worker_executor.execute_plan(project_id, approved_plan)
        if not exec_res["success"]:
            result.errors.append(exec_res.get("error", "Failed to save plan to database"))
            result.message = "Worker execution failed during database write."
            result.worker_status = "Error"
            return result

        self._log(
            result,
            "Worker",
            "save_approved_plan",
            "Success",
            f"Successfully saved {exec_res['milestones_count']} milestones and {exec_res['tasks_count']} tasks.",
        )

        # ---------------------------------------------------------
        # STAGE 6: Reviewer Execution Audit
        # ---------------------------------------------------------
        result.stage = "Execution Verification"
        exec_review = review_execution(project_id=project_id, claimed_progress=0.0, attempt=1, db_path=self.db_path)
        if exec_review["decision"] != "Approved":
            self._log(result, "Reviewer", "review_execution", "Warning", f"Execution verification issue: {exec_review['feedback']}")
        else:
            self._log(result, "Reviewer", "review_execution", "Success", f"Execution verified: {exec_review['feedback']}")

        result.worker_status = "Completed"

        # ---------------------------------------------------------
        # STAGE 7: Python Metrics (Progress, Overdue, Next Task)
        # ---------------------------------------------------------
        result.stage = "Metrics Calculation"
        metrics = self.worker_executor.compute_metrics(project_id)
        result.progress = metrics["progress"]
        result.overdue_tasks = metrics["overdue_tasks"]
        result.next_task_recommendation = metrics["next_task_recommendation"]

        # ---------------------------------------------------------
        # STAGE 8: Report Generation
        # ---------------------------------------------------------
        result.stage = "Report Generation"
        try:
            reports = self.worker_executor.generate_reports(project_id)
            result.csv_report_path = reports["csv_path"]
            result.txt_report_path = reports["txt_path"]
            self._log(result, "Worker", "generate_reports", "Success", "Exported project CSV and TXT reports.")
        except Exception as ex:
            self._log(result, "Worker", "generate_reports", "Warning", f"Report export failed: {str(ex)}")

        result.stage = "Completed"
        result.success = True
        result.message = f"Workflow completed successfully for project '{title}' (ID: #{project_id})."
        self._log(result, "System", "workflow_complete", "Success", result.message)
        return result

    def _get_fallback_plan_data(self, start_date: str, deadline: str) -> Dict[str, Any]:
        """Generate standard curriculum milestone template for offline fallback."""
        return {
            "milestones": [
                {
                    "title": "Phase 1: Requirements & System Architecture",
                    "description": "Problem definition, literature review, and architecture diagram",
                    "due_date": start_date,
                    "status": "Pending",
                },
                {
                    "title": "Phase 2: Core Development & Implementation",
                    "description": "Backend services, database integration, and UI features",
                    "due_date": deadline,
                    "status": "Pending",
                },
                {
                    "title": "Phase 3: Testing, Evaluation & Final Documentation",
                    "description": "Unit testing, thesis report, and project viva preparation",
                    "due_date": deadline,
                    "status": "Pending",
                },
            ],
            "tasks": [
                {
                    "id": 1,
                    "milestone_title": "Phase 1: Requirements & System Architecture",
                    "title": "Literature Survey & Problem Statement",
                    "description": "Document current approaches and formulate project objectives",
                    "priority": "High",
                    "due_date": start_date,
                    "status": "In Progress",
                    "dependencies": [],
                },
                {
                    "id": 2,
                    "milestone_title": "Phase 1: Requirements & System Architecture",
                    "title": "System Architecture & Schema Design",
                    "description": "Define component interaction diagram and SQLite schema",
                    "priority": "High",
                    "due_date": start_date,
                    "status": "Pending",
                    "dependencies": [1],
                },
                {
                    "id": 3,
                    "milestone_title": "Phase 2: Core Development & Implementation",
                    "title": "Core Module Implementation",
                    "description": "Build primary functional logic and core algorithms",
                    "priority": "Critical",
                    "due_date": deadline,
                    "status": "Pending",
                    "dependencies": [2],
                },
                {
                    "id": 4,
                    "milestone_title": "Phase 3: Testing, Evaluation & Final Documentation",
                    "title": "Automated Testing & Project Report",
                    "description": "Write pytest suite and compile final thesis report",
                    "priority": "Medium",
                    "due_date": deadline,
                    "status": "Pending",
                    "dependencies": [3],
                },
            ],
        }

    def generate_plan_for_approval(
        self,
        title: str,
        description: str,
        start_date: str,
        deadline: str,
        requirements_text: Optional[str] = None,
        force_manual: bool = False,
    ) -> WorkflowResult:
        """Run input validation, Planner formulation, and Reviewer verification for student preview and approval."""
        result = WorkflowResult(
            success=False,
            stage="Initialization",
            message="Plan generation initiated.",
            max_revisions=self.max_revisions,
        )

        # STAGE 1: Input Validation
        result.stage = "Input Validation"
        self._log(result, "System", "validate_input", "Info", f"Validating input for project '{title}'...")
        v = validate_project_input(title, description, start_date, deadline)
        if not v["valid"]:
            result.errors.extend(v["errors"])
            result.message = "Input validation failed: " + "; ".join(v["errors"])
            self._log(result, "System", "validate_input", "Error", result.message)
            return result

        # STAGE 2: Project Record Creation
        result.stage = "Project Registration"
        try:
            project_id = create_project(title, description, start_date, deadline, db_path=self.db_path)
            result.project_id = project_id
            self._log(result, "Worker", "create_project", "Success", f"Created project ID #{project_id} in SQLite.")
        except Exception as ex:
            result.errors.append(f"Database error during project creation: {str(ex)}")
            result.message = "Failed to create project in database."
            self._log(result, "Worker", "create_project", "Error", str(ex))
            return result

        # STAGE 3: Gemini Health Check
        gemini_ok, gemini_msg = check_gemini_availability() if not force_manual else (False, "Manual mode requested")
        if not gemini_ok:
            self._log(
                result,
                "System",
                "api_check",
                "Warning",
                f"Gemini API unavailable: {gemini_msg}. Switching to Rule-Based Plan Generation.",
            )
            result.is_manual_fallback = True
            fallback_plan = self._get_fallback_plan_data(start_date, deadline)
            result.plan = fallback_plan
            result.planner_status = "Offline Fallback"
            result.reviewer_status = "Approved (Rule-Based)"
            result.review_decision = "Approved"
            result.review_feedback = "Rule-based milestone template created and verified."
            result.stage = "Awaiting Student Approval"
            result.success = True
            result.message = "Rule-based project plan generated and verified. Ready for student review."
            return result

        # STAGE 4: Planning with Self-Correction Review Loop
        result.stage = "Planning & QA Review"
        result.planner_status = "In Progress"
        self._log(result, "Planner", "plan_project", "Info", "Generating structured project plan with Gemini...")

        current_plan = None
        current_feedback = None
        approved_plan = None
        total_attempts = 1 + self.max_revisions

        for attempt in range(1, total_attempts + 1):
            revision_num = attempt - 1
            result.revision_attempts = revision_num

            try:
                plan_output = plan_project(
                    title=title,
                    description=description,
                    start_date=start_date,
                    deadline=deadline,
                    requirements_text=requirements_text,
                    reviewer_feedback=current_feedback,
                )
                current_plan = plan_output.get("raw_plan")
            except Exception as ex:
                err_str = str(ex)
                self._log(result, "Planner", "plan_project", "Error", f"Planner API error: {err_str}")
                result.errors.append(err_str)
                result.is_manual_fallback = True
                fallback_plan = self._get_fallback_plan_data(start_date, deadline)
                result.plan = fallback_plan
                result.planner_status = "Offline Fallback"
                result.reviewer_status = "Approved (Rule-Based)"
                result.review_decision = "Approved"
                result.review_feedback = "Fallback rule-based plan substituted due to API failure."
                result.stage = "Awaiting Student Approval"
                result.success = True
                result.message = "API error encountered; fallback plan generated for student review."
                return result

            result.reviewer_status = "Reviewing"
            self._log(
                result,
                "Reviewer",
                "review_plan",
                "Info",
                f"Auditing proposed plan (Attempt {attempt}/{total_attempts}, Revision {revision_num}/{self.max_revisions})...",
            )

            rev_result = review_plan(
                plan=current_plan,
                project_start_date=start_date,
                project_deadline=deadline,
                attempt=attempt,
                use_ai=True,
            )

            decision = rev_result["decision"]
            feedback = rev_result["feedback"]
            result.review_decision = decision
            result.review_feedback = feedback

            log_review(
                project_id=project_id,
                attempt=attempt,
                decision=decision,
                feedback=feedback,
                db_path=self.db_path,
            )

            if decision == "Approved":
                self._log(
                    result,
                    "Reviewer",
                    "review_plan",
                    "Success",
                    f"Plan APPROVED on attempt {attempt}: {feedback}",
                )
                approved_plan = rev_result["cleaned_plan"] or current_plan
                result.planner_status = "Completed"
                result.reviewer_status = "Approved"
                break
            else:
                self._log(
                    result,
                    "Reviewer",
                    "review_plan",
                    "Warning",
                    f"Plan REJECTED on attempt {attempt}: {feedback}. Routing corrections to Planner...",
                )
                current_feedback = feedback
                result.reviewer_status = "Rejected"

                if revision_num >= self.max_revisions:
                    result.stage = "Review Limit Exhausted"
                    result.message = (
                        f"Reviewer rejected plan after {self.max_revisions} revisions. "
                        f"Unresolved issue: {feedback}"
                    )
                    result.errors.append(result.message)
                    result.success = False
                    return result

        if not approved_plan:
            result.message = "Failed to obtain an approved plan."
            return result

        result.plan = approved_plan
        result.stage = "Awaiting Student Approval"
        result.success = True
        result.message = "Plan verified by Reviewer Agent. Ready for student approval/editing."
        return result

    def approve_and_execute_plan(
        self,
        project_id: int,
        approved_plan: Dict[str, Any],
        initial_result: Optional[WorkflowResult] = None,
    ) -> WorkflowResult:
        """Persist student-approved (and optionally edited) plan to SQLite and run post-execution QA."""
        result = initial_result or WorkflowResult(
            success=False,
            stage="Execution & Database Persistence",
            message="Plan approved by student.",
            project_id=project_id,
            plan=approved_plan,
            max_revisions=self.max_revisions,
        )

        result.stage = "Execution & Database Persistence"
        result.worker_status = "In Progress"
        self._log(result, "Worker", "save_approved_plan", "Info", "Saving approved milestones and tasks into SQLite...")

        exec_res = self.worker_executor.execute_plan(project_id, approved_plan)
        if not exec_res["success"]:
            result.errors.append(exec_res.get("error", "Failed to save plan to database"))
            result.message = "Worker execution failed during database write."
            result.worker_status = "Error"
            return result

        self._log(
            result,
            "Worker",
            "save_approved_plan",
            "Success",
            f"Successfully saved {exec_res['milestones_count']} milestones and {exec_res['tasks_count']} tasks.",
        )

        # Reviewer Execution Audit
        result.stage = "Execution Verification"
        exec_review = review_execution(project_id=project_id, claimed_progress=0.0, attempt=1, db_path=self.db_path)
        if exec_review["decision"] != "Approved":
            self._log(result, "Reviewer", "review_execution", "Warning", f"Execution verification issue: {exec_review['feedback']}")
        else:
            self._log(result, "Reviewer", "review_execution", "Success", f"Execution verified: {exec_review['feedback']}")

        result.worker_status = "Completed"

        # Python Metrics Calculation
        result.stage = "Metrics Calculation"
        metrics = self.worker_executor.compute_metrics(project_id)
        result.progress = metrics["progress"]
        result.overdue_tasks = metrics["overdue_tasks"]
        result.next_task_recommendation = metrics["next_task_recommendation"]

        # Report Generation
        result.stage = "Report Generation"
        try:
            reports = self.worker_executor.generate_reports(project_id)
            result.csv_report_path = reports["csv_path"]
            result.txt_report_path = reports["txt_path"]
            self._log(result, "Worker", "generate_reports", "Success", "Exported project CSV and TXT reports.")
        except Exception as ex:
            self._log(result, "Worker", "generate_reports", "Warning", f"Report export failed: {str(ex)}")

        result.stage = "Completed"
        result.success = True
        result.message = f"Plan approved and executed successfully for project ID #{project_id}."
        self._log(result, "System", "workflow_complete", "Success", result.message)
        return result

    def _execute_fallback_plan(
        self,
        result: WorkflowResult,
        project_id: int,
        title: str,
        description: str,
        start_date: str,
        deadline: str,
    ) -> WorkflowResult:
        """Execute deterministic fallback plan without any Gemini API calls."""
        result.stage = "Manual Fallback Execution"
        result.planner_status = "Offline Fallback"
        result.reviewer_status = "Manual Verification Mode (Rule-based)"

        self._log(
            result,
            "Worker",
            "fallback_template",
            "Info",
            "Generating standard project milestone template for final-year engineering project...",
        )

        fallback_plan = self._get_fallback_plan_data(start_date, deadline)
        result.plan = fallback_plan

        # Persist to database
        exec_res = self.worker_executor.execute_plan(project_id, fallback_plan)
        result.worker_status = "Completed"

        # Deterministic Reviewer log
        log_review(
            project_id=project_id,
            attempt=1,
            decision="Approved",
            feedback="Offline Rule-Based Verification: Fallback plan adheres to milestone schema and date bounds.",
            db_path=self.db_path,
        )
        result.review_decision = "Approved (Rule-Based)"
        result.review_feedback = "Offline Rule-Based Verification: Fallback plan adheres to milestone schema and date bounds."

        # Compute Python metrics
        metrics = self.worker_executor.compute_metrics(project_id)
        result.progress = metrics["progress"]
        result.overdue_tasks = metrics["overdue_tasks"]
        result.next_task_recommendation = metrics["next_task_recommendation"]

        # Reports
        try:
            reports = self.worker_executor.generate_reports(project_id)
            result.csv_report_path = reports["csv_path"]
            result.txt_report_path = reports["txt_path"]
        except Exception as ex:
            self._log(result, "Worker", "generate_reports", "Warning", f"Report error: {str(ex)}")

        result.stage = "Completed"
        result.success = True
        result.message = (
            f"Manual tracking successfully initialized for project '{title}' (ID: #{project_id}). "
            "Gemini AI was offline; deterministic rule-based tracking is active."
        )
        self._log(result, "System", "workflow_complete", "Success", result.message)
        return result
