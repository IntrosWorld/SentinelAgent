from pathlib import Path

import pytest

from sentinel_agent.office_files import OfficeFileError, OfficeFileManager
from sentinel_agent.planner import HybridPlanner


def test_office_paths_cannot_escape_workspace(tmp_path):
    manager = OfficeFileManager(tmp_path)
    with pytest.raises(OfficeFileError, match="inside the project workspace"):
        manager._path(str(tmp_path.parent / "outside.pdf"), ".pdf")


def test_create_file_plans_are_format_specific():
    xlsx = HybridPlanner._fallback_plan(
        "create excel file results.xlsx with content scores"
    )
    docx = HybridPlanner._fallback_plan(
        "create word file report.docx with content summary"
    )
    pdf = HybridPlanner._fallback_plan(
        "create pdf file report.pdf with content summary"
    )
    assert xlsx.actions[0].name == "create_xlsx"
    assert docx.actions[0].name == "create_docx"
    assert pdf.actions[0].name == "create_pdf"


def test_edit_file_plans_require_mutating_actions():
    xlsx = HybridPlanner._fallback_plan("set B3 to 42 in results.xlsx")
    docx = HybridPlanner._fallback_plan(
        "append report.docx with content conclusion"
    )
    pdf = HybridPlanner._fallback_plan("append report.pdf with content appendix")
    assert xlsx.actions[0].name == "edit_xlsx"
    assert docx.actions[0].name == "append_docx"
    assert pdf.actions[0].name == "append_pdf"
