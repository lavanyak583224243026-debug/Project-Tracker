"""Database access and management functions for SQLite."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Ensure environment variables are loaded
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

DEFAULT_DB_NAME = "project_tracker.db"


def get_database_path(override_path: Optional[str] = None) -> Path:
    """Return the absolute path to the SQLite database file."""
    if override_path:
        return Path(override_path).resolve()
    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        p = Path(env_path)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()
    return (BASE_DIR / DEFAULT_DB_NAME).resolve()


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database schema from schema.sql idempotently."""
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at: {schema_path}")

    schema_sql = schema_path.read_text(encoding="utf-8")
    target_path = get_database_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create and return a SQLite database connection with row factory and foreign keys enabled."""
    target_path = get_database_path(db_path)
    # Ensure directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Ensure tables exist
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
    if not cursor.fetchone():
        schema_path = Path(__file__).resolve().parent / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.commit()

    return conn


# ==============================================================================
# Projects
# ==============================================================================

def create_project(
    title: str,
    description: str,
    start_date: str,
    deadline: str,
    db_path: Optional[str] = None,
) -> int:
    """Create a new project record and return its ID."""
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO projects (title, description, start_date, deadline, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title.strip(), (description or "").strip(), start_date.strip(), deadline.strip(), now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_project(project_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a project by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_projects(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all projects ordered by creation time descending."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]


def delete_project(project_id: int, db_path: Optional[str] = None) -> bool:
    """Delete a project and its cascaded entities."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        return cursor.rowcount > 0


# ==============================================================================
# Milestones
# ==============================================================================

def save_milestone(
    project_id: int,
    title: str,
    description: str = "",
    due_date: Optional[str] = None,
    status: str = "Pending",
    db_path: Optional[str] = None,
) -> int:
    """Save a milestone for a project."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO milestones (project_id, title, description, due_date, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, title.strip(), (description or "").strip(), due_date, status),
        )
        conn.commit()
        return int(cursor.lastrowid)


def save_milestones_bulk(
    project_id: int,
    milestones: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> List[int]:
    """Save multiple milestones within a transaction."""
    ids = []
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for m in milestones:
            cursor.execute(
                """
                INSERT INTO milestones (project_id, title, description, due_date, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    m.get("title", "").strip(),
                    m.get("description", "").strip(),
                    m.get("due_date"),
                    m.get("status", "Pending"),
                ),
            )
            ids.append(int(cursor.lastrowid))
        conn.commit()
    return ids


def get_milestones(project_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all milestones for a project."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM milestones WHERE project_id = ? ORDER BY id ASC", (project_id,))
        return [dict(row) for row in cursor.fetchall()]


# ==============================================================================
# Tasks
# ==============================================================================

def save_task(
    project_id: int,
    milestone_id: Optional[int],
    title: str,
    description: str,
    priority: str,
    due_date: str,
    status: str = "Pending",
    db_path: Optional[str] = None,
) -> int:
    """Save a single task."""
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (project_id, milestone_id, title, description, priority, due_date, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                milestone_id,
                title.strip(),
                (description or "").strip(),
                priority.capitalize() if priority else "Medium",
                due_date.strip(),
                status,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def save_tasks_bulk(
    project_id: int,
    tasks: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> List[int]:
    """Save multiple tasks and their dependencies within a transaction."""
    now = datetime.now().isoformat()
    created_ids = []
    # Temporary mapping of title or temporary id to db id
    title_to_id = {}

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for t in tasks:
            cursor.execute(
                """
                INSERT INTO tasks (project_id, milestone_id, title, description, priority, due_date, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    t.get("milestone_id"),
                    t.get("title", "").strip(),
                    t.get("description", "").strip(),
                    (t.get("priority") or "Medium").capitalize(),
                    t.get("due_date", "").strip(),
                    t.get("status", "Pending"),
                    now,
                    now,
                ),
            )
            new_id = int(cursor.lastrowid)
            created_ids.append(new_id)
            title_to_id[t.get("title", "").strip().lower()] = new_id
            if "temp_id" in t:
                title_to_id[str(t["temp_id"])] = new_id

        # Insert dependencies
        for idx, t in enumerate(tasks):
            current_id = created_ids[idx]
            deps = t.get("dependencies", []) or t.get("depends_on", [])
            for dep in deps:
                target_id = None
                if isinstance(dep, int):
                    # Check if it was a temp_id or real id
                    target_id = title_to_id.get(str(dep), dep)
                elif isinstance(dep, str):
                    dep_clean = dep.strip()
                    if dep_clean.isdigit():
                        target_id = title_to_id.get(dep_clean, int(dep_clean))
                    else:
                        target_id = title_to_id.get(dep_clean.lower())

                if target_id and target_id != current_id:
                    # Ignore insert duplicate
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
                        VALUES (?, ?)
                        """,
                        (current_id, target_id),
                    )

        conn.commit()
    return created_ids


def update_task_status(
    task_id: int,
    status: str,
    db_path: Optional[str] = None,
) -> bool:
    """Update task status (Pending, In Progress, Completed, Blocked)."""
    valid_statuses = {"Pending", "In Progress", "Completed", "Blocked"}
    norm_status = status.strip().title()
    if norm_status not in valid_statuses:
        # Check case variations
        mapping = {
            "pending": "Pending",
            "in progress": "In Progress",
            "completed": "Completed",
            "blocked": "Blocked",
        }
        norm_status = mapping.get(status.strip().lower(), "Pending")

    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (norm_status, now, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_tasks(project_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all tasks for a project with milestone titles."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.*, m.title AS milestone_title
            FROM tasks t
            LEFT JOIN milestones m ON t.milestone_id = m.id
            WHERE t.project_id = ?
            ORDER BY t.due_date ASC, t.id ASC
            """,
            (project_id,),
        )
        tasks = [dict(row) for row in cursor.fetchall()]

        # Attach dependencies to each task
        for task in tasks:
            cursor.execute(
                """
                SELECT depends_on_task_id
                FROM task_dependencies
                WHERE task_id = ?
                """,
                (task["id"],),
            )
            task["dependencies"] = [row["depends_on_task_id"] for row in cursor.fetchall()]
        return tasks


def get_task(task_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a single task by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        task = dict(row)
        cursor.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?", (task_id,))
        task["dependencies"] = [r["depends_on_task_id"] for r in cursor.fetchall()]
        return task


# ==============================================================================
# Dependencies
# ==============================================================================

def add_task_dependency(task_id: int, depends_on_task_id: int, db_path: Optional[str] = None) -> bool:
    """Add a dependency relationship between tasks."""
    if task_id == depends_on_task_id:
        return False
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
            VALUES (?, ?)
            """,
            (task_id, depends_on_task_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_task_dependencies(project_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all dependency relations for a project."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT td.task_id, td.depends_on_task_id,
                   t1.title AS task_title, t2.title AS depends_on_title
            FROM task_dependencies td
            JOIN tasks t1 ON td.task_id = t1.id
            JOIN tasks t2 ON td.depends_on_task_id = t2.id
            WHERE t1.project_id = ?
            """,
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


# ==============================================================================
# Logs: Review and Execution
# ==============================================================================

def log_review(
    project_id: int,
    attempt: int,
    decision: str,
    feedback: str = "",
    db_path: Optional[str] = None,
) -> int:
    """Record a review attempt and verdict in review_logs."""
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO review_logs (project_id, attempt, decision, feedback, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, attempt, decision, feedback or "", now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_review_logs(project_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve review history for a project."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM review_logs WHERE project_id = ? ORDER BY id ASC",
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def log_execution(
    agent_role: str,
    action: str,
    status: str,
    message: str,
    db_path: Optional[str] = None,
) -> int:
    """Record an agent execution event in execution_logs."""
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO execution_logs (agent_role, action, status, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_role, action, status, message, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_execution_logs(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve recent execution logs."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM execution_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
