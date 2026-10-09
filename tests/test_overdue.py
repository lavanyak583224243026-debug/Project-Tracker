"""Unit tests for overdue detection and dependency-aware next-task recommendations."""
import pytest
from database.db import add_task_dependency, create_project, init_db, save_task, update_task_status
from tools.project_tools import detect_overdue_tasks, recommend_next_task


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_overdue.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def test_detect_overdue_tasks(temp_db):
    """Tasks past their due date must be flagged as overdue unless Completed."""
    p_id = create_project("Overdue Test", "Testing overdue logic", "2026-01-01", "2026-05-01", db_path=temp_db)

    # Reference date: 2026-03-10
    # Task 1: Due 2026-03-01, Pending -> Overdue by 9 days
    t1_id = save_task(p_id, None, "Task 1", "Desc", "High", "2026-03-01", status="Pending", db_path=temp_db)
    # Task 2: Due 2026-03-05, Completed -> NOT overdue
    t2_id = save_task(p_id, None, "Task 2", "Desc", "Critical", "2026-03-05", status="Completed", db_path=temp_db)
    # Task 3: Due 2026-03-20, Pending -> Future, NOT overdue
    t3_id = save_task(p_id, None, "Task 3", "Desc", "Low", "2026-03-20", status="Pending", db_path=temp_db)
    # Task 4: Due 2026-02-20, In Progress -> Overdue by 18 days
    t4_id = save_task(p_id, None, "Task 4", "Desc", "Medium", "2026-02-20", status="In Progress", db_path=temp_db)

    overdue = detect_overdue_tasks(p_id, reference_date="2026-03-10", db_path=temp_db)
    assert len(overdue) == 2

    # Sorted by most overdue first: Task 4 (18 days) then Task 1 (9 days)
    assert overdue[0]["id"] == t4_id
    assert overdue[0]["days_overdue"] == 18
    assert overdue[1]["id"] == t1_id
    assert overdue[1]["days_overdue"] == 9


def test_recommend_next_task_dependency_awareness(temp_db):
    """A task cannot be recommended if any task it depends on is incomplete."""
    p_id = create_project("Dependency Project", "Testing dependencies", "2026-01-01", "2026-05-01", db_path=temp_db)

    # Task 1: Pending (pre-requisite)
    t1_id = save_task(p_id, None, "Step 1", "Foundation", "High", "2026-02-01", status="Pending", db_path=temp_db)
    # Task 2: Critical priority, but depends on Task 1
    t2_id = save_task(p_id, None, "Step 2", "Core", "Critical", "2026-02-10", status="Pending", db_path=temp_db)
    add_task_dependency(t2_id, t1_id, db_path=temp_db)

    # Initially, Task 1 must be recommended because Task 2 has an unmet dependency
    rec1 = recommend_next_task(p_id, reference_date="2026-01-15", db_path=temp_db)
    assert rec1 is not None
    assert rec1["task"]["id"] == t1_id

    # Now mark Task 1 as Completed
    update_task_status(t1_id, "Completed", db_path=temp_db)

    # Now Task 2's dependency is met, so Task 2 should be recommended (Critical priority)
    rec2 = recommend_next_task(p_id, reference_date="2026-01-15", db_path=temp_db)
    assert rec2 is not None
    assert rec2["task"]["id"] == t2_id


def test_recommend_next_task_in_progress_priority(temp_db):
    """An 'In Progress' task takes priority over 'Pending' tasks of same or lower priority."""
    p_id = create_project("Priority Project", "Testing priority", "2026-01-01", "2026-05-01", db_path=temp_db)

    save_task(p_id, None, "Pending Task", "Desc", "High", "2026-03-01", status="Pending", db_path=temp_db)
    t_in_prog = save_task(p_id, None, "Active Task", "Desc", "High", "2026-03-05", status="In Progress", db_path=temp_db)

    rec = recommend_next_task(p_id, db_path=temp_db)
    assert rec["task"]["id"] == t_in_prog
    assert "In Progress" in rec["task"]["status"]


def test_recommend_all_tasks_blocked(temp_db):
    """Safely report when all remaining tasks are blocked by pending dependencies."""
    p_id = create_project("Blocked Project", "Testing blocked state", "2026-01-01", "2026-05-01", db_path=temp_db)

    t1_id = save_task(p_id, None, "Blocked Task 1", "Desc", "High", "2026-03-01", status="Blocked", db_path=temp_db)
    t2_id = save_task(p_id, None, "Dependent Task 2", "Desc", "Critical", "2026-03-05", status="Pending", db_path=temp_db)
    add_task_dependency(t2_id, t1_id, db_path=temp_db)

    rec = recommend_next_task(p_id, db_path=temp_db)
    assert rec["task"] is None
    assert "blocked" in rec["reason"].lower()
