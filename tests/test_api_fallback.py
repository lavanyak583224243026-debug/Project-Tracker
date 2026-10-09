"""Tests for API error handling, quota errors, and deterministic manual fallback."""
from unittest.mock import patch
import pytest

from crew.orchestrator import ProjectWorkflowOrchestrator
from database.db import get_project, get_tasks, init_db, update_task_status
from tools.project_tools import (
    calculate_progress,
    detect_overdue_tasks,
    export_csv_report,
    export_txt_report,
    recommend_next_task,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_fallback.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def test_missing_api_key_triggers_manual_fallback(temp_db):
    """When API key is absent or placeholder, workflow smoothly falls back to rule-based tracking."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db)

    with patch("services.gemini_service.get_api_key", return_value=""):
        result = orchestrator.run_full_workflow(
            title="Offline Project",
            description="Testing without API key",
            start_date="2026-02-01",
            deadline="2026-06-01",
        )

        assert result.success is True
        assert result.is_manual_fallback is True
        assert result.project_id is not None
        assert "Manual tracking successfully initialized" in result.message
        assert result.reviewer_status == "Manual Verification Mode (Rule-based)"

        # Verify fallback milestones and tasks saved in database
        tasks = get_tasks(result.project_id, db_path=temp_db)
        assert len(tasks) > 0
        assert result.progress is not None
        assert result.csv_report_path is not None


def test_gemini_429_quota_exceeded_fallback(temp_db):
    """HTTP 429 Resource Exhausted during planning falls back to offline mode gracefully."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db)

    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", side_effect=RuntimeError("Gemini Quota Exceeded (429): Rate limit reached.")):

        result = orchestrator.run_full_workflow(
            title="Quota Exceeded Test",
            description="Testing 429 recovery",
            start_date="2026-01-01",
            deadline="2026-05-01",
        )

        assert result.success is True
        assert result.is_manual_fallback is True
        assert result.project_id is not None
        tasks = get_tasks(result.project_id, db_path=temp_db)
        assert len(tasks) > 0


def test_gemini_503_service_unavailable_fallback(temp_db):
    """HTTP 503 Server Unavailable during planning falls back gracefully."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db)

    with patch("crew.orchestrator.check_gemini_availability", return_value=(True, "Online")), \
         patch("crew.orchestrator.plan_project", side_effect=RuntimeError("Gemini Server Error (503): Service temporarily unavailable.")):

        result = orchestrator.run_full_workflow(
            title="503 Service Test",
            description="Testing 503 recovery",
            start_date="2026-01-01",
            deadline="2026-05-01",
        )

        assert result.success is True
        assert result.is_manual_fallback is True
        assert result.project_id is not None


def test_manual_mode_full_lifecycle_offline(temp_db):
    """Verify manual tracking lifecycle operates entirely offline without LLM."""
    orchestrator = ProjectWorkflowOrchestrator(db_path=temp_db)

    # Force manual
    result = orchestrator.run_full_workflow(
        title="Manual Embedded Robotics",
        description="Autonomous rover",
        start_date="2026-01-01",
        deadline="2026-06-01",
        force_manual=True,
    )

    assert result.success is True
    assert result.is_manual_fallback is True
    pid = result.project_id

    # 1. Update task statuses manually
    tasks = get_tasks(pid, db_path=temp_db)
    assert len(tasks) >= 3
    t1_id = tasks[0]["id"]
    update_task_status(t1_id, "Completed", db_path=temp_db)

    # 2. Recalculate progress
    prog = calculate_progress(pid, db_path=temp_db)
    assert prog["completed_tasks"] == 1
    assert prog["progress_percentage"] > 0.0

    # 3. Next task recommendation
    next_t = recommend_next_task(pid, db_path=temp_db)
    assert next_t is not None

    # 4. Reports export offline
    csv_file = export_csv_report(pid, db_path=temp_db)
    txt_file = export_txt_report(pid, db_path=temp_db)
    assert csv_file.endswith(".csv")
    assert txt_file.endswith(".txt")


def test_no_secrets_exposed_in_logs():
    """Ensure sensitive tokens or keys are never exposed in log output."""
    from services.gemini_service import check_gemini_availability
    with patch("services.gemini_service.get_api_key", return_value="AIzaSySuperSecretKey123456789"):
        with patch("google.genai.Client", side_effect=Exception("Failed with AIzaSySuperSecretKey123456789")):
            ok, msg = check_gemini_availability()
            assert ok is False
            assert "AIzaSySuperSecretKey123456789" not in msg
