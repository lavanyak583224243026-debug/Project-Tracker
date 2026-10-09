"""Unit tests for SQLite database schema and operations."""
import sqlite3
import pytest
from database.db import (
    add_task_dependency,
    create_project,
    delete_project,
    get_db_connection,
    get_execution_logs,
    get_milestones,
    get_project,
    get_review_logs,
    get_task,
    get_task_dependencies,
    get_tasks,
    init_db,
    list_projects,
    log_execution,
    log_review,
    save_milestone,
    save_milestones_bulk,
    save_task,
    save_tasks_bulk,
    update_task_status,
)


@pytest.fixture
def temp_db(tmp_path):
    """Fixture providing an isolated temporary SQLite database path."""
    db_file = tmp_path / "test_tracker.db"
    db_path = str(db_file)
    init_db(db_path)
    return db_path


def test_schema_initialization(temp_db):
    """Verify all tables and foreign keys are properly initialized."""
    with get_db_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

    expected_tables = {
        "projects",
        "milestones",
        "tasks",
        "task_dependencies",
        "review_logs",
        "execution_logs",
    }
    assert expected_tables.issubset(tables)


def test_project_crud(temp_db):
    """Test creating, reading, listing, and deleting projects."""
    p_id = create_project(
        title="Autonomous Drone",
        description="Final year drone project",
        start_date="2026-01-01",
        deadline="2026-05-31",
        db_path=temp_db,
    )
    assert p_id > 0

    p = get_project(p_id, db_path=temp_db)
    assert p is not None
    assert p["title"] == "Autonomous Drone"
    assert p["deadline"] == "2026-05-31"

    projects = list_projects(db_path=temp_db)
    assert len(projects) == 1

    deleted = delete_project(p_id, db_path=temp_db)
    assert deleted is True
    assert get_project(p_id, db_path=temp_db) is None


def test_milestone_and_task_operations(temp_db):
    """Test saving milestones and tasks with statuses and dependencies."""
    p_id = create_project(
        title="AI Chatbot",
        description="NLP chatbot",
        start_date="2026-02-01",
        deadline="2026-06-30",
        db_path=temp_db,
    )

    m_id = save_milestone(
        project_id=p_id,
        title="Milestone 1: Prototype",
        description="Initial demo",
        due_date="2026-03-15",
        status="Pending",
        db_path=temp_db,
    )
    assert m_id > 0

    t1_id = save_task(
        project_id=p_id,
        milestone_id=m_id,
        title="Data Collection",
        description="Gather corpus",
        priority="High",
        due_date="2026-03-01",
        status="Completed",
        db_path=temp_db,
    )

    t2_id = save_task(
        project_id=p_id,
        milestone_id=m_id,
        title="Model Fine-tuning",
        description="Fine-tune transformer",
        priority="Critical",
        due_date="2026-03-15",
        status="Pending",
        db_path=temp_db,
    )

    # Add dependency: t2 depends on t1
    dep_added = add_task_dependency(t2_id, t1_id, db_path=temp_db)
    assert dep_added is True

    # Retrieve tasks
    tasks = get_tasks(p_id, db_path=temp_db)
    assert len(tasks) == 2
    t2_retrieved = next(t for t in tasks if t["id"] == t2_id)
    assert t1_id in t2_retrieved["dependencies"]

    # Update task status
    updated = update_task_status(t2_id, "In Progress", db_path=temp_db)
    assert updated is True
    t2_updated = get_task(t2_id, db_path=temp_db)
    assert t2_updated["status"] == "In Progress"


def test_foreign_key_cascade_deletion(temp_db):
    """Verify that deleting a project cascades to its tasks and milestones."""
    p_id = create_project(
        title="Test Project",
        description="Desc",
        start_date="2026-01-01",
        deadline="2026-06-01",
        db_path=temp_db,
    )
    m_id = save_milestone(p_id, "M1", db_path=temp_db)
    t_id = save_task(p_id, m_id, "T1", "Desc", "Medium", "2026-02-01", db_path=temp_db)

    # Delete project
    delete_project(p_id, db_path=temp_db)

    assert get_milestones(p_id, db_path=temp_db) == []
    assert get_tasks(p_id, db_path=temp_db) == []


def test_review_and_execution_logs(temp_db):
    """Test writing and reading review logs and execution audit logs."""
    p_id = create_project(
        title="Log Test",
        description="Desc",
        start_date="2026-01-01",
        deadline="2026-06-01",
        db_path=temp_db,
    )

    r_id = log_review(
        project_id=p_id,
        attempt=1,
        decision="Approved",
        feedback="All criteria met.",
        db_path=temp_db,
    )
    assert r_id > 0
    reviews = get_review_logs(p_id, db_path=temp_db)
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "Approved"

    e_id = log_execution(
        agent_role="Worker",
        action="update_task",
        status="Success",
        message="Task 1 set to Completed",
        db_path=temp_db,
    )
    assert e_id > 0
    exec_logs = get_execution_logs(limit=10, db_path=temp_db)
    assert len(exec_logs) >= 1
    assert exec_logs[0]["action"] == "update_task"
