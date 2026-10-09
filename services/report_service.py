"""Service for generating and managing downloadable project reports."""
from pathlib import Path
from typing import Optional, Tuple
from tools.project_tools import export_csv_report, export_txt_report, get_project_summary


def generate_csv_report_data(
    project_id: int,
    db_path: Optional[str] = None,
) -> Tuple[str, str]:
    """Generate CSV report content as string suitable for download or export.

    Returns (filename, csv_text).
    """
    summary = get_project_summary(project_id, db_path=db_path)
    if "error" in summary:
        raise ValueError(summary["error"])

    title = summary["project"]["title"].replace(" ", "_").lower()
    filename = f"report_project_{project_id}_{title}.csv"

    # Temporary export to extract content
    temp_path = export_csv_report(project_id, db_path=db_path)
    content = Path(temp_path).read_text(encoding="utf-8")
    return filename, content


def generate_txt_report_data(
    project_id: int,
    db_path: Optional[str] = None,
) -> Tuple[str, str]:
    """Generate text report content suitable for download or export.

    Returns (filename, report_text).
    """
    summary = get_project_summary(project_id, db_path=db_path)
    if "error" in summary:
        raise ValueError(summary["error"])

    title = summary["project"]["title"].replace(" ", "_").lower()
    filename = f"report_project_{project_id}_{title}.txt"

    temp_path = export_txt_report(project_id, db_path=db_path)
    content = Path(temp_path).read_text(encoding="utf-8")
    return filename, content
