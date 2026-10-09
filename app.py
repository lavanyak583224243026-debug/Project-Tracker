"""Streamlit Web Application: Final-Year Project Tracker Agent.

Features:
- Multi-agent autonomous workflow (Planner, Worker, Reviewer) using Google Gemini
- Deterministic SQLite task database with real-time updates
- Human-in-the-loop plan review & manual fallback mode
- Overdue task alerts & dependency-aware next-task recommendations
- Executive CSV and TXT report downloads
"""
import io
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Ensure environment variables are loaded
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

from crew.orchestrator import ProjectWorkflowOrchestrator
from database.db import (
    create_project,
    get_execution_logs,
    get_milestones,
    get_project,
    get_review_logs,
    get_task,
    get_tasks,
    init_db,
    list_projects,
    save_milestone,
    save_task,
    update_task_status,
)
from services.gemini_service import check_gemini_availability, get_model_name
from services.report_service import generate_csv_report_data, generate_txt_report_data
from tools.project_tools import (
    calculate_progress,
    detect_overdue_tasks,
    parse_requirements,
    recommend_next_task,
    validate_project_input,
)

# Page configuration
st.set_page_config(
    page_title="Final-Year Project Tracker Agent",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize database
init_db()

# Custom CSS for styling
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 16px;
        border-left: 5px solid #1f77b4;
        margin-bottom: 12px;
    }
    .agent-card {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .badge-completed { background-color: #28a745; color: white; padding: 2px 8px; border-radius: 4px; }
    .badge-progress { background-color: #007bff; color: white; padding: 2px 8px; border-radius: 4px; }
    .badge-pending { background-color: #ffc107; color: black; padding: 2px 8px; border-radius: 4px; }
    .badge-blocked { background-color: #dc3545; color: white; padding: 2px 8px; border-radius: 4px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_sidebar():
    """Render the sidebar with API status, active project selector, and manual quick actions."""
    st.sidebar.title("🎓 Project Tracker")
    st.sidebar.caption("CrewAI + Google Gemini Multi-Agent System")

    # API Health Status indicator
    is_gemini_up, status_msg = check_gemini_availability()
    if is_gemini_up:
        st.sidebar.success(f"🟢 **Gemini API:** Connected ({get_model_name()})")
    else:
        st.sidebar.warning(f"🟡 **Gemini API:** Offline / Fallback Mode\n\n*{status_msg}*")

    st.sidebar.markdown("---")

    # Project selection
    projects = list_projects()
    project_options = {p["id"]: f"#{p['id']} - {p['title']}" for p in projects}

    selected_project_id = None
    if project_options:
        selected_project_id = st.sidebar.selectbox(
            "Select Active Project",
            options=list(project_options.keys()),
            format_func=lambda x: project_options[x],
            key="active_project_id",
        )
    else:
        st.sidebar.info("No projects in database yet. Create one on the main screen.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("System Architecture")
    st.sidebar.markdown(
        """
        - **Planner Agent:** Requirement decomposition & structured JSON plan
        - **Worker Agent:** SQLite persistence, metrics & reports
        - **Reviewer Agent:** QA audit & self-correction loop (max 2 revisions)
        """
    )
    return selected_project_id


def render_project_overview(project_id: int):
    """Render detailed view of an active project."""
    project = get_project(project_id)
    if not project:
        st.error(f"Project #{project_id} not found.")
        return

    milestones = get_milestones(project_id)
    tasks = get_tasks(project_id)
    progress_data = calculate_progress(project_id)
    overdue_tasks = detect_overdue_tasks(project_id)
    next_task_info = recommend_next_task(project_id)
    review_logs = get_review_logs(project_id)

    # Header section
    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.subheader(f"📌 {project['title']}")
        if project.get("description"):
            st.markdown(f"*{project['description']}*")
        st.caption(f"🗓️ Timeline: **{project['start_date']}** to **{project['deadline']}**")
    with col_h2:
        # Report download buttons
        try:
            csv_name, csv_data = generate_csv_report_data(project_id)
            txt_name, txt_data = generate_txt_report_data(project_id)
            st.download_button("📥 Download CSV Report", data=csv_data, file_name=csv_name, mime="text/csv", use_container_width=True)
            st.download_button("📄 Download TXT Summary", data=txt_data, file_name=txt_name, mime="text/plain", use_container_width=True)
        except Exception as ex:
            st.error(f"Report preparation error: {str(ex)}")

    st.markdown("---")

    # Metrics Bar
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        st.metric("Total Tasks", progress_data["total_tasks"])
    with col_m2:
        st.metric("Completed Tasks", progress_data["completed_tasks"])
    with col_m3:
        st.metric("Progress", f"{progress_data['progress_percentage']}%")
    with col_m4:
        st.metric("Overdue Tasks", len(overdue_tasks), delta=f"-{len(overdue_tasks)}" if overdue_tasks else "0", delta_color="inverse")

    st.progress(float(progress_data["progress_percentage"]) / 100.0)
    st.caption(f"📐 Calculation Logic: {progress_data['method']}")

    # Overdue Alert Banner
    if overdue_tasks:
        st.error(
            f"⚠️ **Attention:** {len(overdue_tasks)} task(s) are currently overdue! Address them immediately to maintain project timeline."
        )
        with st.expander("🚨 View Overdue Task List", expanded=True):
            overdue_df = pd.DataFrame([
                {
                    "Task ID": o["id"],
                    "Title": o["title"],
                    "Milestone": o["milestone_title"],
                    "Due Date": o["due_date"],
                    "Days Overdue": o["days_overdue"],
                    "Priority": o["priority"],
                    "Status": o["status"],
                }
                for o in overdue_tasks
            ])
            st.dataframe(overdue_df, use_container_width=True, hide_index=True)

    # Next Task Recommendation Card
    st.markdown("### 🎯 Recommended Next Task")
    if next_task_info and next_task_info.get("task"):
        rec_task = next_task_info["task"]
        st.info(
            f"👉 **Task #{rec_task['id']}: {rec_task['title']}**\n\n"
            f"- **Priority:** `{rec_task.get('priority')}` | **Due:** `{rec_task.get('due_date')}` | **Status:** `{rec_task.get('status')}`\n"
            f"- **Milestone:** {rec_task.get('milestone_title', 'General')}\n"
            f"- **Rationale:** {next_task_info.get('reason')}"
        )
    else:
        reason = next_task_info.get("reason") if next_task_info else "All tasks are completed or no tasks recorded."
        st.success(f"✨ {reason}")

    # Tabs for Tasks, Milestones, Status Updates, QA Review Logs
    tab_tasks, tab_milestones, tab_update, tab_qa = st.tabs([
        "📋 Tasks List",
        "🚩 Milestones",
        "✏️ Update Task Status",
        "🔍 QA Review History",
    ])

    with tab_tasks:
        if tasks:
            task_rows = []
            for t in tasks:
                deps = ", ".join(map(str, t.get("dependencies", [])))
                task_rows.append({
                    "ID": t["id"],
                    "Title": t["title"],
                    "Milestone": t.get("milestone_title", "Unassigned"),
                    "Priority": t["priority"],
                    "Due Date": t["due_date"],
                    "Status": t["status"],
                    "Dependencies": deps or "None",
                    "Description": t.get("description", ""),
                })
            df_tasks = pd.DataFrame(task_rows)
            st.dataframe(df_tasks, use_container_width=True, hide_index=True)
        else:
            st.info("No tasks recorded for this project yet.")

    with tab_milestones:
        if milestones:
            m_rows = [
                {
                    "ID": m["id"],
                    "Title": m["title"],
                    "Description": m.get("description", ""),
                    "Due Date": m.get("due_date", "N/A"),
                    "Status": m.get("status", "Pending"),
                }
                for m in milestones
            ]
            st.dataframe(pd.DataFrame(m_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No milestones registered yet.")

    with tab_update:
        st.subheader("Update Task Status")
        if tasks:
            col_u1, col_u2, col_u3 = st.columns([2, 1, 1])
            with col_u1:
                selected_t_id = st.selectbox(
                    "Choose Task",
                    options=[t["id"] for t in tasks],
                    format_func=lambda tid: f"#{tid} - {next((t['title'] for t in tasks if t['id'] == tid), '')}",
                )
            with col_u2:
                curr_task = next(t for t in tasks if t["id"] == selected_t_id)
                new_status = st.selectbox(
                    "New Status",
                    options=["Pending", "In Progress", "Completed", "Blocked"],
                    index=["Pending", "In Progress", "Completed", "Blocked"].index(curr_task["status"])
                    if curr_task["status"] in ["Pending", "In Progress", "Completed", "Blocked"]
                    else 0,
                )
            with col_u3:
                st.write("")
                st.write("")
                if st.button("💾 Save Status", use_container_width=True):
                    update_task_status(selected_t_id, new_status)
                    st.success(f"Task #{selected_t_id} marked as '{new_status}'!")
                    st.rerun()
        else:
            st.info("Add tasks to allow status updates.")

    with tab_qa:
        st.subheader("Reviewer Agent Audit Logs")
        if review_logs:
            for r in review_logs:
                decision_badge = "🟢 Approved" if r["decision"] == "Approved" else "🔴 Rejected"
                with st.chat_message("assistant"):
                    st.markdown(f"**Attempt #{r['attempt']} - {decision_badge}** ({r['created_at']})")
                    st.markdown(r["feedback"])
        else:
            st.info("No QA reviews recorded yet for this project.")


def render_workflow_runner():
    """Render the project planning and autonomous workflow execution form."""
    st.header("🚀 Create Project & Run Multi-Agent Workflow")
    st.markdown(
        "Enter your final-year project topic. The **Planner Agent** will analyze requirements, "
        "the **Reviewer Agent** will validate deadlines and dependencies with bounded self-correction, "
        "and the **Worker Agent** will persist tasks to SQLite."
    )

    with st.form("project_creation_form"):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            title = st.text_input("Project Title *", placeholder="e.g., Autonomous Quadcopter Navigation System")
            start_date_val = st.date_input("Project Start Date *", value=date.today())
            team_members = st.text_input("Team Members (Optional)", placeholder="e.g., Alice (Lead), Bob (Backend)")
        with col_f2:
            deadline_val = st.date_input("Project Final Deadline *", value=date.today() + timedelta(days=90))
            uploaded_doc = st.file_uploader(
                "Upload Requirements / Syllabus Document (Optional)",
                type=["txt", "md", "pdf"],
                help="Accepts .txt, .md, or .pdf files containing project specifications.",
            )

        description = st.text_area(
            "Project Abstract & Scope *",
            placeholder="Describe the problem, technologies (e.g. Python, ROS, PyTorch), and core features...",
            height=120,
        )

        col_b1, col_b2, col_b3 = st.columns([2, 2, 1])
        with col_b1:
            run_btn = st.form_submit_button("⚡ Run Full Autonomous Workflow", type="primary", use_container_width=True)
        with col_b2:
            plan_only_btn = st.form_submit_button("📝 Plan & Review First (Human-in-the-Loop)", use_container_width=True)
        with col_b3:
            force_manual = st.checkbox("Offline Mode", value=False)

    # Human-in-the-Loop Plan Generation Trigger
    if plan_only_btn:
        start_date_str = start_date_val.strftime("%Y-%m-%d")
        deadline_str = deadline_val.strftime("%Y-%m-%d")

        req_text = ""
        if uploaded_doc is not None:
            bytes_data = uploaded_doc.read()
            req_text = parse_requirements(bytes_data, uploaded_doc.name)

        status_box = st.status("Formulating and Auditing Plan (Human-in-the-Loop)...", expanded=True)
        with status_box:
            st.write("🔍 **Validating Inputs and Date Bounds...**")
            v = validate_project_input(title, description, start_date_str, deadline_str)
            if not v["valid"]:
                status_box.update(label="❌ Input Validation Failed", state="error")
                for err in v["errors"]:
                    st.error(err)
                return

            orchestrator = ProjectWorkflowOrchestrator(max_revisions=2)
            st.write("🧠 **Planner Agent:** Generating milestones, tasks, and dependencies...")
            st.write("🔎 **Reviewer Agent:** Validating date bounds and dependencies...")

            staged_res = orchestrator.generate_plan_for_approval(
                title=title,
                description=description,
                start_date=start_date_str,
                deadline=deadline_str,
                requirements_text=req_text,
                force_manual=force_manual,
            )

            if staged_res.success:
                status_box.update(label="✅ Plan generated and approved by Reviewer Agent!", state="complete")
                st.session_state["staged_workflow"] = staged_res
                st.rerun()
            else:
                status_box.update(label=f"⚠️ {staged_res.message}", state="error")
                for err in staged_res.errors:
                    st.error(err)

    # 1-Click Autonomous Workflow Trigger
    if run_btn:
        start_date_str = start_date_val.strftime("%Y-%m-%d")
        deadline_str = deadline_val.strftime("%Y-%m-%d")

        # Parse uploaded requirement file if present
        req_text = ""
        if uploaded_doc is not None:
            bytes_data = uploaded_doc.read()
            req_text = parse_requirements(bytes_data, uploaded_doc.name)

        # Status placeholders
        st.markdown("---")
        status_box = st.status("Executing Multi-Agent Autonomous Workflow...", expanded=True)

        with status_box:
            st.write("🔍 **Validating Inputs and Date Bounds...**")
            v = validate_project_input(title, description, start_date_str, deadline_str)
            if not v["valid"]:
                status_box.update(label="❌ Input Validation Failed", state="error")
                for err in v["errors"]:
                    st.error(err)
                return

            st.write("🤖 **Initializing ProjectWorkflowOrchestrator...**")
            orchestrator = ProjectWorkflowOrchestrator(max_revisions=2)

            st.write("🧠 **Planner Agent:** Formulating milestones, tasks, and dependency graph...")
            st.write("🔎 **Reviewer Agent:** Checking date bounds & verifying self-correction...")
            st.write("⚙️ **Worker Agent:** Persisting approved entities into SQLite...")

            result = orchestrator.run_full_workflow(
                title=title,
                description=description,
                start_date=start_date_str,
                deadline=deadline_str,
                requirements_text=req_text,
                force_manual=force_manual,
            )

            if result.success:
                status_box.update(label=f"✅ {result.message}", state="complete")
                st.session_state["active_project_id"] = result.project_id
                if "staged_workflow" in st.session_state:
                    del st.session_state["staged_workflow"]
            else:
                status_box.update(label=f"⚠️ Workflow stopped: {result.stage}", state="error")

        # Display agent cards and decisions
        st.markdown("### 🎭 Multi-Agent Execution Summary")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                f"""
                <div class="agent-card">
                    <h4>📋 Planner Agent</h4>
                    <p>Status: <strong>{result.planner_status}</strong></p>
                    <p>Revision Attempts: <strong>{result.revision_attempts} / {result.max_revisions}</strong></p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"""
                <div class="agent-card">
                    <h4>🔍 Reviewer Agent</h4>
                    <p>Status: <strong>{result.reviewer_status}</strong></p>
                    <p>Decision: <strong>{result.review_decision or 'N/A'}</strong></p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f"""
                <div class="agent-card">
                    <h4>⚙️ Worker Agent</h4>
                    <p>Status: <strong>{result.worker_status}</strong></p>
                    <p>Database: <strong>SQLite Persisted</strong></p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if result.review_feedback:
            st.info(f"**Reviewer Audit Remarks:** {result.review_feedback}")

        # Display tool logs
        with st.expander("📜 Agent Tool Execution Logs", expanded=False):
            for log_entry in result.logs:
                st.text(log_entry)

        if result.success and result.project_id:
            st.success(f"Project #{result.project_id} loaded successfully. View details below.")
            render_project_overview(result.project_id)

    # Human-in-the-Loop Staged Plan Review & Approval UI
    if "staged_workflow" in st.session_state:
        staged_result = st.session_state["staged_workflow"]
        st.markdown("---")
        st.subheader("🧑‍🏫 Student Review & Human-in-the-Loop Plan Approval")
        st.markdown(
            "The **Planner Agent** generated this plan and the **Reviewer Agent** audited it. "
            "You can review and modify tasks below before approving execution."
        )

        st.markdown(
            f"""
            <div class="agent-card">
                <h4>🔍 Reviewer Agent Quality Audit</h4>
                <p>Status: <strong>{staged_result.reviewer_status}</strong> | Decision: <strong>{staged_result.review_decision}</strong></p>
                <p>Feedback: <em>{staged_result.review_feedback}</em></p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### 🚩 Planned Milestones")
        m_df = pd.DataFrame(staged_result.plan.get("milestones", []))
        st.dataframe(m_df, use_container_width=True, hide_index=True)

        st.markdown("#### ✏️ Editable Tasks List (Modify Titles, Due Dates, Priorities)")
        raw_tasks = staged_result.plan.get("tasks", [])
        tasks_df = pd.DataFrame(raw_tasks)
        edited_tasks_df = st.data_editor(
            tasks_df,
            key="hitl_task_editor",
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "priority": st.column_config.SelectboxColumn("Priority", options=["Critical", "High", "Medium", "Low"]),
                "due_date": st.column_config.TextColumn("Due Date (YYYY-MM-DD)"),
            },
        )

        col_app1, col_app2 = st.columns([2, 1])
        with col_app1:
            if st.button("✅ Approve & Persist Plan to Database", type="primary", use_container_width=True):
                records = edited_tasks_df.to_dict(orient="records")
                for r in records:
                    if isinstance(r.get("dependencies"), str):
                        try:
                            s = r["dependencies"].replace("[", "").replace("]", "").strip()
                            r["dependencies"] = [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]
                        except Exception:
                            r["dependencies"] = []
                    elif not isinstance(r.get("dependencies"), list):
                        r["dependencies"] = []

                approved_plan = {
                    "milestones": staged_result.plan.get("milestones", []),
                    "tasks": records,
                }

                orchestrator = ProjectWorkflowOrchestrator(max_revisions=2)
                with st.spinner("Worker Agent saving entities to SQLite & computing metrics..."):
                    exec_res = orchestrator.approve_and_execute_plan(
                        project_id=staged_result.project_id,
                        approved_plan=approved_plan,
                        initial_result=staged_result,
                    )

                if exec_res.success:
                    st.success(f"🎉 Plan approved and persisted! Project ID #{exec_res.project_id}")
                    st.session_state["active_project_id"] = exec_res.project_id
                    del st.session_state["staged_workflow"]
                    st.rerun()
                else:
                    st.error(f"Execution error: {exec_res.message}")

        with col_app2:
            if st.button("❌ Discard Plan", use_container_width=True):
                del st.session_state["staged_workflow"]
                st.warning("Plan discarded.")
                st.rerun()


def render_manual_entry_tab():
    """Tab for manual project and task entry without AI."""
    st.header("✍️ Manual Project & Task Management")
    st.caption("Create custom milestones and tasks directly without calling Gemini.")

    tab_new_proj, tab_new_task, tab_new_m = st.tabs([
        "➕ New Project",
        "➕ Add Task to Existing Project",
        "➕ Add Milestone",
    ])

    with tab_new_proj:
        with st.form("manual_project_form"):
            p_title = st.text_input("Project Title")
            p_desc = st.text_area("Description")
            c1, c2 = st.columns(2)
            with c1:
                p_start = st.date_input("Start Date", value=date.today())
            with c2:
                p_end = st.date_input("Deadline", value=date.today() + timedelta(days=60))
            if st.form_submit_button("Create Project Manually"):
                s_str = p_start.strftime("%Y-%m-%d")
                d_str = p_end.strftime("%Y-%m-%d")
                val = validate_project_input(p_title, p_desc, s_str, d_str)
                if not val["valid"]:
                    st.error("; ".join(val["errors"]))
                else:
                    new_pid = create_project(p_title, p_desc, s_str, d_str)
                    st.success(f"Created project #{new_pid}: {p_title}")
                    st.rerun()

    projects = list_projects()
    if not projects:
        st.info("No projects created yet.")
        return

    proj_dict = {p["id"]: f"#{p['id']} - {p['title']}" for p in projects}

    with tab_new_m:
        with st.form("manual_milestone_form"):
            target_pid_m = st.selectbox("Select Project", options=list(proj_dict.keys()), format_func=lambda x: proj_dict[x])
            m_title = st.text_input("Milestone Title", placeholder="e.g. Phase 1: Prototype")
            m_desc = st.text_input("Milestone Description")
            m_due = st.date_input("Due Date", value=date.today() + timedelta(days=30))
            if st.form_submit_button("Save Milestone"):
                save_milestone(target_pid_m, m_title, m_desc, m_due.strftime("%Y-%m-%d"))
                st.success("Milestone saved!")
                st.rerun()

    with tab_new_task:
        with st.form("manual_task_form"):
            target_pid = st.selectbox("Select Project", options=list(proj_dict.keys()), format_func=lambda x: proj_dict[x], key="task_target_proj")
            milestones = get_milestones(target_pid)
            m_opts = {None: "None / General"}
            for m in milestones:
                m_opts[m["id"]] = m["title"]

            t_title = st.text_input("Task Title", placeholder="e.g. Implement REST API")
            t_desc = st.text_area("Description")
            c_t1, c_t2, c_t3 = st.columns(3)
            with c_t1:
                t_milestone = st.selectbox("Milestone", options=list(m_opts.keys()), format_func=lambda x: m_opts[x])
            with c_t2:
                t_priority = st.selectbox("Priority", ["Critical", "High", "Medium", "Low"])
            with c_t3:
                t_due_date = st.date_input("Due Date", value=date.today() + timedelta(days=14))

            if st.form_submit_button("Save Task"):
                if not t_title.strip():
                    st.error("Task title cannot be empty.")
                else:
                    save_task(
                        project_id=target_pid,
                        milestone_id=t_milestone,
                        title=t_title,
                        description=t_desc,
                        priority=t_priority,
                        due_date=t_due_date.strftime("%Y-%m-%d"),
                        status="Pending",
                    )
                    st.success(f"Task '{t_title}' added to Project #{target_pid}!")
                    st.rerun()


# Main Navigation
selected_proj_id = render_sidebar()

main_tab1, main_tab2, main_tab3 = st.tabs([
    "📊 Active Project Dashboard",
    "⚡ Run Multi-Agent Workflow",
    "✍️ Manual Management",
])

with main_tab1:
    if selected_proj_id:
        render_project_overview(selected_proj_id)
    else:
        st.info("👋 Welcome! Use the 'Run Multi-Agent Workflow' tab to plan a new final-year project, or select an existing project from the sidebar.")

with main_tab2:
    render_workflow_runner()

with main_tab3:
    render_manual_entry_tab()
