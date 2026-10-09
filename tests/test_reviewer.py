"""Unit tests for Reviewer validation and decision logic."""
import pytest
from agents.reviewer import review_execution, review_plan
from database.db import create_project, get_review_logs, init_db, save_milestone, save_task


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_reviewer.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def test_reviewer_approves_valid_plan():
    """Valid plan within project date bounds must be approved."""
    valid_plan = {
        "milestones": [
            {
                "title": "Phase 1: Research",
                "description": "Background study",
                "due_date": "2026-02-15",
            }
        ],
        "tasks": [
            {
                "id": 1,
                "milestone_title": "Phase 1: Research",
                "title": "Literature Review",
                "description": "Read 10 papers",
                "priority": "High",
                "due_date": "2026-02-10",
                "dependencies": [],
            }
        ],
    }

    result = review_plan(
        plan=valid_plan,
        project_start_date="2026-01-01",
        project_deadline="2026-05-31",
        attempt=1,
        use_ai=False,  # Test deterministic QA rule
    )

    assert result["decision"] == "Approved"
    assert result["target_agent"] == "None"
    assert len(result["errors"]) == 0


def test_reviewer_rejects_plan_out_of_bounds_date():
    """Plan with task due date past project deadline must be rejected and targeted to Planner."""
    invalid_plan = {
        "milestones": [
            {"title": "Phase 1", "due_date": "2026-02-01"}
        ],
        "tasks": [
            {
                "id": 1,
                "milestone_title": "Phase 1",
                "title": "Past Deadline Task",
                "priority": "High",
                "due_date": "2026-07-01",  # Past deadline 2026-05-31
                "dependencies": [],
            }
        ],
    }

    result = review_plan(
        plan=invalid_plan,
        project_start_date="2026-01-01",
        project_deadline="2026-05-31",
        attempt=1,
        use_ai=False,
    )

    assert result["decision"] == "Rejected"
    assert result["target_agent"] == "Planner"
    assert any("outside project timeline" in err for err in result["errors"])


def test_reviewer_rejects_self_dependency_loop():
    """Plan with a task depending on itself must be rejected."""
    loop_plan = {
        "milestones": [{"title": "Phase 1", "due_date": "2026-02-01"}],
        "tasks": [
            {
                "id": 1,
                "milestone_title": "Phase 1",
                "title": "Circular Task",
                "priority": "High",
                "due_date": "2026-01-20",
                "dependencies": [1],  # self-dependency!
            }
        ],
    }

    result = review_plan(
        plan=loop_plan,
        project_start_date="2026-01-01",
        project_deadline="2026-05-31",
        attempt=1,
        use_ai=False,
    )

    assert result["decision"] == "Rejected"
    assert result["target_agent"] == "Planner"
    assert any("self-dependency loop" in err for err in result["errors"])


def test_reviewer_execution_audit(temp_db):
    """Reviewer checks actual SQLite state vs claimed progress."""
    p_id = create_project("Audit Project", "Desc", "2026-01-01", "2026-05-01", db_path=temp_db)
    save_milestone(p_id, "M1", db_path=temp_db)
    save_task(p_id, None, "T1", "Desc", "High", "2026-02-01", status="Completed", db_path=temp_db)
    save_task(p_id, None, "T2", "Desc", "Medium", "2026-02-02", status="Pending", db_path=temp_db)

    # Actual progress is 50.0%. If Worker claimed 100.0%, Reviewer rejects
    bad_res = review_execution(project_id=p_id, claimed_progress=100.0, attempt=1, db_path=temp_db)
    assert bad_res["decision"] == "Rejected"
    assert bad_res["target_agent"] == "Worker"
    assert "Progress mismatch" in bad_res["feedback"]

    # Accurate claim passes
    good_res = review_execution(project_id=p_id, claimed_progress=50.0, attempt=2, db_path=temp_db)
    assert good_res["decision"] == "Approved"

    # Verify review logs persisted
    logs = get_review_logs(p_id, db_path=temp_db)
    assert len(logs) == 2
