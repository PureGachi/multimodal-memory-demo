import json
import subprocess
from pathlib import Path

import pytest

from memory_app.governance.release import audit, scan, forbidden, REQUIRED


def command(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def repo(tmp_path):
    command(tmp_path, "init", "-b", "main")
    command(tmp_path, "config", "user.name", "Fixture Only")
    command(tmp_path, "config", "user.email", "fixture@example.invalid")
    for name in REQUIRED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture only\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.2.0"\n', encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\ndata/\n", encoding="utf-8")
    command(tmp_path, "add", ".")
    command(tmp_path, "commit", "-m", "test fixture")
    return tmp_path


def test_clean_commit_is_distinct_from_public_deployment(repo):
    before = command(repo, "rev-parse", "HEAD")
    result = audit(repo, expect_commit=before)
    assert result["fixed_local_commit_ready"]
    assert not result["public_remote_verified"] and not result["pushed_by_this_tool"]
    assert command(repo, "rev-parse", "HEAD") == before
    assert not command(repo, "status", "--porcelain")
    assert "expected_commit_mismatch" in audit(repo, expect_commit="not-the-hash")["blockers"]


def test_dirty_worktree_is_not_a_fixed_version_and_private_env_is_not_read(repo):
    (repo / ".env").write_text("DO_NOT_READ_IGNORED_ENV", encoding="utf-8")
    (repo / "new.py").write_text("print(1)\n", encoding="utf-8")
    result = audit(repo)
    assert result["candidate_content_check_passed"] and not result["fixed_local_commit_ready"]
    assert "uncommitted_changes" in result["blockers"]
    assert ".env" not in [f["path"] for f in result["candidate_files"]]


def test_removed_secret_is_still_found_in_history_without_value_disclosure(repo):
    token = "sk-" + "a1" * 25  # synthetic fixture, never a real key
    path = repo / "bad.txt"
    path.write_text(token, encoding="utf-8")
    command(repo, "add", "bad.txt")
    command(repo, "commit", "-m", "synthetic history fixture")
    path.unlink()
    command(repo, "add", "-u")
    command(repo, "commit", "-m", "remove fixture")
    result = audit(repo)
    assert result["history_findings"] and not result["fixed_local_commit_ready"]
    assert token not in json.dumps(result)


@pytest.mark.parametrize("name", [".env", ".env.deploy", "data/report.json", ".conda/python.exe", "backup.sqlite3", "secret.pem"])
def test_private_paths_are_not_publishable(name):
    assert forbidden(name)


def test_samples_and_source_are_allowed_but_values_are_scanned():
    assert not forbidden(".env.competition.example")
    assert not forbidden("src/memory_app/governance/retention.py")
    raw = ("VISION_API_KEY=" + "sk-" + "a1" * 25).encode()
    findings = scan(".env.example", raw)
    assert findings and all("path" in f and "code" in f for f in findings)
    assert raw.decode() not in json.dumps(findings)


def test_review_exceptions_are_exact_content_and_rule_bound(repo):
    import hashlib
    raw = ("sk-" + "a1" * 25).encode()
    (repo / "fixture.txt").write_bytes(raw)
    (repo / "docs/scan-exceptions.json").write_text(json.dumps([{
        "path": "fixture.txt", "code": "api_key_like", "sha256": hashlib.sha256(raw).hexdigest(),
        "reason": "synthetic fixture only"}]), encoding="utf-8")
    result = audit(repo)
    assert result["candidate_content_check_passed"] and result["reviewed_exceptions"]
    (repo / "fixture.txt").write_bytes(raw + b" changed")
    assert not audit(repo)["candidate_content_check_passed"]


def test_release_version_matches_package_metadata_and_health(tmp_path):
    import tomllib
    from fastapi.testclient import TestClient
    from memory_app import __version__
    from memory_app.config import Settings
    from memory_app.main import create_app
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__ == "0.3.0"
    with TestClient(create_app(Settings(tmp_path, "none"))) as api:
        assert api.get("/health").json()["version"] == __version__
        assert api.get("/openapi.json").json()["info"]["version"] == __version__
