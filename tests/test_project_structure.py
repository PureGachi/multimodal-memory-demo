"""Module-boundary checks; no .env, real models or dataset downloads."""
import ast
from pathlib import Path
import subprocess
import sys

import pytest

from memory_app.evaluation import metrics, reporting

ROOT = Path(__file__).resolve().parents[1]


def test_evaluation_cli_reexports_shared_functions_without_duplicate_implementations():
    from test_mem_gallery_evaluation import evaluation
    assert evaluation.rank_metrics is metrics.rank_metrics
    assert evaluation.summarize is metrics.summarize
    assert evaluation.summarize_images is metrics.summarize_images
    assert evaluation.markdown_report is reporting.markdown_report
    tree = ast.parse((ROOT / "scripts/evaluate_mem_gallery.py").read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert not names.intersection({"rank_metrics", "summarize", "summarize_images", "markdown_report"})


def test_report_and_metrics_are_pure_modules():
    for module in (metrics, reporting):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name == "json" for alias in node.names)
            assert not isinstance(node, ast.ImportFrom)


def test_comparison_imports_package_functions_not_another_cli():
    tree = ast.parse((ROOT / "scripts/compare_rrf.py").read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "evaluate_mem_gallery" not in modules
    assert {"memory_app.evaluation.metrics", "memory_app.evaluation.reporting"} <= modules


def test_application_package_never_imports_command_line_scripts():
    script_names = {p.stem for p in (ROOT / "scripts").glob("*.py")}
    for path in (ROOT / "src/memory_app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] not in {"scripts", *script_names}, str(path)
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] not in {"scripts", *script_names} for a in node.names), str(path)


@pytest.mark.parametrize("script", ["evaluate_mem_gallery.py", "compare_rrf.py"])
def test_original_cli_help_still_works_without_running_models(script):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"],
                            cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 0
    assert b"usage:" in result.stdout


def test_formal_directory_defaults_agree_without_running_setup_scripts():
    suffix = "official/evaluation/runtime"
    for name in ("scripts/enable_competition.ps1", ".env.competition.example", "Dockerfile", "deploy/compose.yaml"):
        assert suffix in (ROOT / name).read_text(encoding="utf-8"), name
