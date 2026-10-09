"""Integration tests for Workflow Orchestrator, revision loops, and agent collaboration."""
from unittest.mock import patch
import pytest

from crew.orchestrator import ProjectWorkflowOrchestrator
from database.db import get_milestones, get_project, get_review_logs, get_tasks, init_db


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_workflow.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def make_valid_plan():
    return {
        "milestones": [
            {
                "title": "M1: Design & Analysis",
                "description": "Design specs",
                "due_date": "2026-03-01",
            },
            {
                "title": "M2: Implementation & Testing",
                "description": "Code and tests",
                "due_date": "2026-05-31",
            },
        ],
        "tasks": [
            {
                "id": 1,
                "milestone_title": "M1: Design & Analysis",
                "title": "Requirement Analysis",
                "description": "Spec document",
                "priority": "High",
                "due_date": "2026-02-15",
                "dependencies": [],
            },
            {
                "id": 2,
                "milestone_title": "M2: Implementation & Testing",
                "title": "Core Service Development",
                "description": "Service coding",
                "priority": "Critical",
                "due_date": "2026-04-15",
                "dependencies": [1],
            },
        ],
    }


def make_invalid_plan():
    return {
        "milestones": [
            {
                "title": "M1: Invalid Phase",
                "description": "Bad plan",
                "due_date": "2026-08-01",  # Past project deadline 2026-05-31
            }
        ],
        "tasks": [
            {
                "id": 1,
                "milestone_title": "M1: Invalid Phase",
                "title": "Late Task",
                "priority": "Medium",
                "due_date": "2026-09-01",  # Past project deadline
                "dependencies": [1],  # Self loop
            }
        ],
    }


def test_full_workflow_success_attempt_1(temp_db):
    """Test standard successful workflow: Planner generates valid plan, approved on attempt 1."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db, max_revisions=2)

    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", return_value={"raw_plan": make_valid_plan(), "is_valid": True}), \
         patch("agents.reviewer.generate_gemini_content", return_value='{"approved": true, "feedback": "Excellent structure"}'):

        result = orchestrator.run_full_workflow(
            title="IoT Smart Agriculture",
            description="Automated soil moisture tracking system",
            start_date="2026-01-15",
            deadline="2026-05-31",
        )

        assert result.success is True
        assert result.stage == "Completed"
        assert result.project_id is not None
        assert result.revision_attempts == 0
        assert result.review_decision == "Approved"
        assert result.planner_status == "Completed"
        assert result.worker_status == "Completed"
        assert result.reviewer_status == "Approved"

        # Verify SQLite data was saved
        p = get_project(result.project_id, db_path=temp_db)
        assert p["title"] == "IoT Smart Agriculture"
        tasks = get_tasks(result.project_id, db_path=temp_db)
        assert len(tasks) == 2
        milestones = get_milestones(result.project_id, db_path=temp_db)
        assert len(milestones) == 2

        # Verify reports generated
        assert result.csv_report_path is not None
        assert result.txt_report_path is not None


def test_workflow_self_correction_bounded_revision(temp_db):
    """Test Reviewer rejection on attempt 1, followed by Planner correction on attempt 2."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db, max_revisions=2)

    attempt_counter = {"count": 0}

    def mock_planner(*args, **kwargs):
        attempt_counter["count"] += 1
        if attempt_counter["count"] == 1:
            # First attempt returns invalid plan (dates out of bounds)
            return {"raw_plan": make_invalid_plan(), "is_valid": False}
        else:
            # Second attempt corrects it
            return {"raw_plan": make_valid_plan(), "is_valid": True}

    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", side_effect=mock_planner), \
         patch("agents.reviewer.generate_gemini_content", return_value='{"approved": true, "feedback": "Revisions accepted"}'):

        result = orchestrator.run_full_workflow(
            title="Self-Correction Project",
            description="Testing agent revision loop",
            start_date="2026-01-01",
            deadline="2026-05-31",
        )

        assert result.success is True
        assert result.stage == "Completed"
        assert result.revision_attempts == 1  # 1 revision was performed
        assert attempt_counter["count"] == 2

        # Review logs must show attempt 1 Rejected, attempt 2 Approved, plus execution review
        logs = get_review_logs(result.project_id, db_path=temp_db)
        assert len(logs) >= 2
        assert logs[0]["decision"] == "Rejected"
        assert logs[1]["decision"] == "Approved"


def test_workflow_exhausts_max_revisions(temp_db):
    """If Planner fails to fix issues after max 2 revisions, workflow must halt gracefully."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db, max_revisions=2)

    # Planner continuously returns invalid plan
    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", return_value={"raw_plan": make_invalid_plan(), "is_valid": False}):

        result = orchestrator.run_full_workflow(
            title="Persistent Failure Project",
            description="Never fixes dates",
            start_date="2026-01-01",
            deadline="2026-05-31",
        )

        assert result.success is False
        assert result.stage == "Review Limit Exhausted"
        assert result.revision_attempts == 2
        assert result.reviewer_status == "Rejected"
        assert "after 2 revisions" in result.errors[0] or "Revision limit of 2 exhausted" in result.errors[0]

        # Incomplete / invalid plan must NOT be saved to SQLite tasks
        tasks = get_tasks(result.project_id, db_path=temp_db)
        assert len(tasks) == 0


def test_human_in_the_loop_approval_and_edit(temp_db):
    """Test human-in-the-loop: Planner generates plan, student edits a task, and Worker saves edited plan."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db, max_revisions=2)

    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", return_value={"raw_plan": make_valid_plan(), "is_valid": True}), \
         patch("agents.reviewer.generate_gemini_content", return_value='{"approved": true, "feedback": "Plan verified"}'):

        # 1. Generate plan for student preview
        staged_result = orchestrator.generate_plan_for_approval(
            title="Human in the Loop Robotics",
            description="Autonomous rover navigation with sensor fusion",
            start_date="2026-01-15",
            deadline="2026-06-30",
        )

        assert staged_result.success is True
        assert staged_result.stage == "Awaiting Student Approval"
        assert staged_result.project_id is not None
        assert staged_result.plan is not None

        # Verify no tasks are saved in SQLite prior to student approval
        initial_tasks = get_tasks(staged_result.project_id, db_path=temp_db)
        assert len(initial_tasks) == 0

        # 2. Simulate Student editing the first task's title and priority
        student_plan = staged_result.plan
        student_plan["tasks"][0]["title"] = "Student Edited: Sensor Calibration Protocol"
        student_plan["tasks"][0]["priority"] = "Critical"

        # 3. Approve and execute the student-modified plan
        final_result = orchestrator.approve_and_execute_plan(
            project_id=staged_result.project_id,
            approved_plan=student_plan,
            initial_result=staged_result,
        )

        assert final_result.success is True
        assert final_result.stage == "Completed"
        assert final_result.worker_status == "Completed"

        # 4. Verify in SQLite that the student-edited task was persisted
        saved_tasks = get_tasks(staged_result.project_id, db_path=temp_db)
        assert len(saved_tasks) == 2
        assert saved_tasks[0]["title"] == "Student Edited: Sensor Calibration Protocol"
        assert saved_tasks[0]["priority"] == "Critical"

        # Verify reports and review log
        assert final_result.csv_report_path is not None
        assert final_result.txt_report_path is not None
        logs = get_review_logs(staged_result.project_id, db_path=temp_db)
        assert len(logs) >= 2  # Plan QA + Execution QA

