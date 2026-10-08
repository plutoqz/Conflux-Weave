"""Unit tests for AI hygiene and bureaucratic ritualism auditor."""

from __future__ import annotations

from pathlib import Path
import tempfile

from conflux_weave.project_tutor.hygiene_auditor import (
    audit_file_content,
    audit_project_hygiene,
)
from conflux_weave.project_tutor.spec import HygieneCategory


def test_audit_silent_exception_swallowing() -> None:
    code = """
def bad_function():
    try:
        critical_operation()
    except Exception:
        pass
    """
    findings = audit_file_content("test_swallow.py", code)
    categories = [f.category for f in findings]
    assert HygieneCategory.SILENT_SWALLOWING in categories
    swallow_finding = next(f for f in findings if f.category == HygieneCategory.SILENT_SWALLOWING)
    assert swallow_finding.severity == "critical"
    assert "--- a/test_swallow.py" in swallow_finding.refactor_diff_suggestion


def test_audit_stub_placeholders() -> None:
    code = """
def uncompleted_service():
    pass
    """
    findings = audit_file_content("test_stub.py", code)
    categories = [f.category for f in findings]
    assert HygieneCategory.STUB_PLACEHOLDER in categories


def test_audit_phantom_unused_arguments() -> None:
    code = """
def query_data(query_str: str, fake_phantom_param: int = 10):
    return query_str.upper()
    """
    findings = audit_file_content("test_phantom.py", code)
    categories = [f.category for f in findings]
    assert HygieneCategory.PHANTOM_CONFIG in categories
    phantom_finding = next(f for f in findings if f.category == HygieneCategory.PHANTOM_CONFIG)
    assert "fake_phantom_param" in phantom_finding.issue_description


def test_audit_zombie_defensiveness_and_secrets() -> None:
    code = """
def process(obj):
    if obj is not None and obj is not None:
        return obj
    api_key = "sk-123456789012345678901234"
    ritual_freeze_hash = "abc12345"
    """
    findings = audit_file_content("test_zombie.py", code)
    categories = [f.category for f in findings]
    assert HygieneCategory.ZOMBIE_DEFENSIVENESS in categories
    assert HygieneCategory.ARCHITECTURAL_VULNERABILITY in categories
    assert HygieneCategory.BUREAUCRATIC_RITUALISM in categories


def test_audit_project_hygiene_e2e() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "clean.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        (root / "dirty.py").write_text("def fail():\n    try:\n        pass\n    except:\n        pass\n", encoding="utf-8")

        report = audit_project_hygiene(root)
        assert report.total_findings_count >= 1
        assert report.hygiene_score < 100
        assert report.executive_verdict is not None
