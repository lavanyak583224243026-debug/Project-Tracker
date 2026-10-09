"""Unit tests for deterministic progress calculations."""
import pytest
from database.db import create_project, init_db, save_task
from tools.project_tools import calculate_progress


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_progress.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def test_progress_zero_tasks(temp_db):
    """Zero tasks must safely yield 0.0% progress without division by zero."""
    p_id = create_project("Empty Project", "No tasks", "2026-01-01", "2026-05-01", db_path=temp_db)
    res = calculate_progress(p_id, db_path=temp_db)
    assert res["total_tasks"] == 0
    assert res["completed_tasks"] == 0
    assert res["progress_percentage"] == 0.0
    assert "0 tasks -> 0.0%" in res["method"]


def test_progress_calculation_accuracies(temp_db):
    """Verify equal-weight progress calculation across various task statuses."""
    p_id = create_project("Active Project", "With tasks", "2026-01-01", "2026-05-01", db_path=temp_db)

    # 4 tasks: 1 Completed, 1 In Progress, 1 Pending, 1 Blocked -> 1/4 = 25.0%
    save_task(p_id, None, "T1", "Desc", "High", "2026-02-01", status="Completed", db_path=temp_db)
    save_task(p_id, None, "T2", "Desc", "Medium", "2026-02-15", status="In Progress", db_path=temp_db)
    save_task(p_id, None, "T3", "Desc", "Low", "2026-03-01", status="Pending", db_path=temp_db)
    save_task(p_id, None, "T4", "Desc", "High", "2026-03-15", status="Blocked", db_path=temp_db)

    res = calculate_progress(p_id, db_path=temp_db)
    assert res["total_tasks"] == 4
    assert res["completed_tasks"] == 1
    assert res["in_progress_tasks"] == 1
    assert res["pending_tasks"] == 1
    assert res["blocked_tasks"] == 1
    assert res["progress_percentage"] == 25.0

    # Add another completed task -> 2/5 = 40.0%
    save_task(p_id, None, "T5", "Desc", "Critical", "2026-04-01", status="Completed", db_path=temp_db)
    res2 = calculate_progress(p_id, db_path=temp_db)
    assert res2["total_tasks"] == 5
    assert res2["completed_tasks"] == 2
    assert res2["progress_percentage"] == 40.0


def test_progress_100_percent(temp_db):
    """All tasks completed yields 100.0%."""
    p_id = create_project("Finished Project", "All done", "2026-01-01", "2026-05-01", db_path=temp_db)
    save_task(p_id, None, "T1", "Desc", "High", "2026-02-01", status="Completed", db_path=temp_db)
    save_task(p_id, None, "T2", "Desc", "High", "2026-02-02", status="Completed", db_path=temp_db)

    res = calculate_progress(p_id, db_path=temp_db)
    assert res["progress_percentage"] == 100.0
