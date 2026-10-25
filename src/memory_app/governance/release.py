"""Read-only Git release audit. No staging, commits, network, .env or model calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib

MAX_FILE = 8 * 1024 * 1024
SAMPLES = {".env.example", ".env.competition.example"}
REQUIRED = {"README.md", "pyproject.toml", "requirements-lock.txt", "LICENSE", "docs/METHOD.md",
            "docs/DATA_RETENTION.md", "docs/RELEASING.md", "Dockerfile", ".dockerignore", ".gitignore"}
PATTERNS = {
    "api_key_like": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    "github_token_like": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})"),
    "aws_access_key_like": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "embedded_url_credentials": re.compile(rb"https?://[^\s/:]+:[^\s/@]+@"),
}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          timeout=60).stdout


def forbidden(name):
    path = PurePosixPath(name)
    if any(p in {"data", ".conda", ".venv", "venv", "build", "dist", "__pycache__", ".pytest_cache"} for p in path.parts):
        return True
    if any(p == ".env" or p.startswith(".env.") for p in path.parts) and name not in SAMPLES:
        return True
    return path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".log", ".pem", ".key", ".p12", ".pfx"}


def scan(name, raw):
    """Findings contain locations and categories only, never matched values."""
    if forbidden(name):
        return [{"path": name, "code": "private_or_generated_path"}]
    if len(raw) > MAX_FILE:
        return [{"path": name, "code": "file_too_large_for_review"}]
    if b"\x00" in raw:
        return [{"path": name, "code": "binary_needs_manual_review"}]
    findings = []
    for code, pattern in PATTERNS.items():
        for match in pattern.finditer(raw):
            findings.append({"path": name, "line": raw.count(b"\n", 0, match.start()) + 1, "code": code})
    # Also catch long token-like literal assignments, while leaving env lookups and
    # documented placeholders alone. This heuristic cannot prove absence of secrets.
    assignment = re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[=:]\s*[\"']?([A-Za-z0-9_/-]{24,})")
    for match in assignment.finditer(raw):
        value = match.group(1)
        if any(c in b"0123456789" for c in value) and any(c in b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in value):
            findings.append({"path": name, "line": raw.count(b"\n", 0, match.start()) + 1,
                             "code": "secret_literal_needs_review"})
    return findings


def tree(root, commit):
    entries = []
    for item in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not item:
            continue
        head, name = item.split(b"\t", 1)
        mode, kind, oid = head.split()
        entries.append((name.decode("utf-8"), mode.decode(), kind.decode(), oid.decode()))
    return entries


def audit(root, *, expect_commit=None):
    root = Path(root).resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("Run at repository root")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    branch = git(root, "branch", "--show-current").decode().strip()
    dirty = bool(git(root, "status", "--porcelain=v1", "-z"))
    names = sorted(set(n.decode("utf-8") for n in git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if n))
    findings, manifest = [], []
    exception_file = root / "docs/scan-exceptions.json"
    exceptions = json.loads(exception_file.read_text(encoding="utf-8")) if exception_file.is_file() else []
    reviewed_exceptions = []
    def reviewed(items, raw):
        # Git text checkout may normalize CRLF. Never normalize binary images.
        digest = hashlib.sha256(raw if b"\x00" in raw else raw.replace(b"\r\n", b"\n")).hexdigest()
        remaining = []
        for item in items:
            approval = next((e for e in exceptions if e.get("path") == item["path"]
                             and e.get("code") == item["code"] and e.get("sha256") == digest
                             and e.get("reason")), None)
            if approval:
                reviewed_exceptions.append({**item, "sha256": digest, "reason": approval["reason"]})
            else:
                remaining.append(item)
        return remaining
    for name in names:
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue  # pending deletion, still scanned in Git history below
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            findings.append({"path": name, "code": "link_or_outside_repository"})
            continue
        if not path.is_file():
            findings.append({"path": name, "code": "unsupported_entry"})
            continue
        if forbidden(name):
            findings.extend(scan(name, b""))  # do not read tracked .env values
            continue
        if path.stat().st_size > MAX_FILE:
            findings.append({"path": name, "code": "file_too_large_for_review"})
            continue
        raw = path.read_bytes()
        findings.extend(reviewed(scan(name, raw), raw))
        manifest.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    # Ignore rules do not remove old secrets: inspect every locally reachable commit.
    commits = git(root, "rev-list", "--all").decode().splitlines()
    checked_blobs = set()
    history_findings = []
    for revision in commits:
        for name, mode, kind, oid in tree(root, revision):
            if forbidden(name):
                history_findings.append({"commit": revision, "path": name, "code": "private_or_generated_path"})
                continue
            if kind != "blob" or mode == "120000":
                history_findings.append({"commit": revision, "path": name, "code": "link_or_submodule_needs_review"})
                continue
            if (oid, name) in checked_blobs:
                continue
            checked_blobs.add((oid, name))
            size = int(git(root, "cat-file", "-s", oid))
            if size > MAX_FILE:
                history_findings.append({"commit": revision, "path": name, "code": "file_too_large_for_review"})
                continue
            raw = git(root, "cat-file", "blob", oid)
            history_findings.extend({"commit": revision, **f} for f in reviewed(scan(name, raw), raw))
    present = {m["path"] for m in manifest}
    missing = sorted(REQUIRED - present)
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    blockers = []
    if findings: blockers.append("working_tree_findings")
    if history_findings: blockers.append("local_history_findings")
    if missing: blockers.append("missing_release_files")
    if dirty: blockers.append("uncommitted_changes")
    if expect_commit and expect_commit != commit: blockers.append("expected_commit_mismatch")
    ready = not findings and not history_findings and not missing
    return {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "read-only-release-audit-v1",
            "repository_root": str(root), "head": commit, "branch": branch, "package_version": version,
            "dirty": dirty, "candidate_content_check_passed": ready, "fixed_local_commit_ready": not blockers,
            "public_remote_verified": False, "pushed_by_this_tool": False,
            "blockers": blockers, "missing_files": missing, "working_tree_findings": findings,
            "reviewed_exceptions": reviewed_exceptions,
            "history_findings": history_findings, "locally_reachable_commits_checked": len(commits),
            "candidate_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            "candidate_files": manifest,
            "limitations": ["heuristic secret checks, not a guarantee", "only locally reachable Git history",
                            "public visibility, remote commit and deployed artifact are not verified",
                            "license choice, attribution and private notes need author review"]}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expect-commit", help="Full exact hash, never a branch name or abbreviated hash")
    parser.add_argument("--output", type=Path, help="Private JSON report under ignored data/governance/releases")
    args = parser.parse_args()
    try:
        result = audit(args.root, expect_commit=args.expect_commit)
        if args.output:
            allowed = args.root.resolve() / "data/governance/releases"
            target = args.output.resolve()
            if not target.is_relative_to(allowed) or target == allowed:
                raise ValueError("Report must stay in private release evidence directory")
            from memory_app.governance.retention import no_links, write_json
            no_links(args.output.absolute())
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("Do not overwrite release evidence")
            write_json(target, result, exclusive=True)
        summary = {k: v for k, v in result.items() if k != "candidate_files"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result["fixed_local_commit_ready"] else 2
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print(json.dumps({"error": "release_audit_failed", "hint": "检查 Git 仓库、路径和文件可读性；不输出文件内容或原始命令错误。"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
