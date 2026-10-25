"""Explicit scoped retention. No scheduler, no implicit deletion, no .env reads."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

MARKER = ".memory-retention.json"
RECEIPT = ".memory-deletion-receipt.json"
POLICY = "official-evaluation-retention-v1"


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result.astimezone(timezone.utc)


def no_links(path):
    """Refuse symlinks and Windows junctions, including ancestor redirects."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Links are not allowed in managed paths")
        if item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError("Reparse points are not allowed in managed paths")


def scope(base, run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id):
        raise ValueError("Invalid run ID")
    base = Path(os.path.abspath(base))
    # A deliberately named parent avoids accidentally targeting a home/repo/data root.
    if base.name != "official":
        raise ValueError("Managed parent directory must be named official")
    no_links(base)
    root = base / run_id
    no_links(root)
    if root.resolve().parent != base.resolve() or root.resolve() == base.resolve():
        raise ValueError("Target escapes managed parent")
    return root


def write_json(path, value, *, exclusive=False):
    no_links(path)
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as out:
            out.write(raw)
        return
    fd, temporary = tempfile.mkstemp(prefix=".retention-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(raw)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def initialize(base, run_id):
    root = scope(base, run_id)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Initialization requires a new or empty dedicated directory")
    root.mkdir(parents=True, exist_ok=True)
    value = {"policy": POLICY, "run_id": run_id, "registration_id": uuid.uuid4().hex,
             "scope": "official-task-data-and-derived-copies", "status": "registered",
             "task_completed_at": None, "delete_by": None,
             "created_at": datetime.now(timezone.utc).isoformat(),
             "rule_source": "https://agentmemories.ai/rules"}
    write_json(root / MARKER, value, exclusive=True)
    return value


def registration(base, run_id):
    root = scope(base, run_id)
    no_links(root / MARKER)
    value = json.loads((root / MARKER).read_text(encoding="utf-8"))
    if value.get("policy") != POLICY or value.get("run_id") != run_id or not value.get("registration_id"):
        raise ValueError("Invalid registration")
    if value.get("task_completed_at"):
        if utc(value["delete_by"]) != utc(value["task_completed_at"]) + timedelta(days=30):
            raise ValueError("Invalid deletion deadline")
    elif value.get("delete_by") is not None:
        raise ValueError("Deadline without completion date")
    return root, value


def complete(base, run_id, at):
    root, value = registration(base, run_id)
    finished = utc(at)
    if finished > datetime.now(timezone.utc):
        raise ValueError("Completion date cannot be in the future")
    if value.get("task_completed_at") or value.get("status") == "deleted":
        raise ValueError("Completion already recorded; cannot postpone deadline")
    value.update(task_completed_at=finished.isoformat(), delete_by=(finished + timedelta(days=30)).isoformat(),
                 status="awaiting_deletion")
    write_json(root / MARKER, value)
    return value


def plan(base, run_id):
    root, value = registration(base, run_id)
    files, directories = [], []
    def visit(folder):
        for path in sorted(folder.iterdir()):
            no_links(path)
            if path.parent == root and path.name in {MARKER, RECEIPT}:
                if not path.is_file():
                    raise ValueError("Reserved metadata name must be a regular file")
                continue
            if path.is_dir():
                directories.append(path.relative_to(root).as_posix())
                visit(path)
            elif path.is_file():
                before = path.stat()
                if before.st_nlink != 1:
                    raise ValueError("Hard-linked data requires separate copy review")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Files changed during planning; stop service first")
                files.append({"path": path.relative_to(root).as_posix(), "bytes": after.st_size,
                              "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest()})
            else:
                raise ValueError("Unsupported filesystem entry")
    visit(root)
    evidence = {"root": str(root.resolve()), "registration": value, "files": files,
                "directories": sorted(directories)}
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {**evidence, "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files),
            "confirm_token": "delete-" + fingerprint,
            "overdue": bool(value.get("delete_by") and datetime.now(timezone.utc) >= utc(value["delete_by"])),
            "deletion_enabled": bool(value.get("task_completed_at") and value.get("status") != "deleted"),
            "external_copies": "operator must separately review backups, snapshots, logs and providers"}


def apply(base, run_id, token, *, service_stopped=False, external_copies_reviewed=False):
    if not service_stopped or not external_copies_reviewed:
        raise ValueError("Stop writers and review external copies before deletion")
    current = plan(base, run_id)
    if not current["deletion_enabled"] or current["confirm_token"] != token:
        raise ValueError("No completed task or preview token is stale")
    root = scope(base, run_id)
    # Revalidate individual files before unlink. Never pass a broad path to rmtree.
    for item in current["files"]:
        path = root / item["path"]
        no_links(path)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Target escaped registered root")
        if (path.stat().st_size, path.stat().st_mtime_ns) != (item["bytes"], item["mtime_ns"]):
            raise ValueError("File changed; generate a fresh plan")
        path.unlink()
    for name in sorted(current["directories"], key=lambda x: len(Path(x).parts), reverse=True):
        path = root / name
        no_links(path)
        path.rmdir()
    receipt = {"policy": POLICY, "run_id": run_id, "deleted_at": datetime.now(timezone.utc).isoformat(),
               "file_count": current["file_count"], "bytes": current["total_bytes"],
               "plan_fingerprint": token, "scope": "registered local files only",
               "external_copies_reviewed": True,
               "limitations": "logical unlink, not secure erasure; external-copy removal is operator-attested"}
    write_json(root / RECEIPT, receipt)
    value = current["registration"]
    value["status"] = "deleted"
    write_json(root / MARKER, value)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "complete", "plan", "check", "apply"])
    parser.add_argument("--base", type=Path, default=Path("data/official"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--at", help="Actual task completion ISO timestamp, including timezone")
    parser.add_argument("--confirm")
    parser.add_argument("--service-stopped", action="store_true")
    parser.add_argument("--external-copies-reviewed", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.base, args.run_id)
        elif args.command == "complete":
            if not args.at:
                raise ValueError("Actual completion timestamp is required")
            result = complete(args.base, args.run_id, args.at)
        elif args.command == "apply":
            result = apply(args.base, args.run_id, args.confirm, service_stopped=args.service_stopped,
                           external_copies_reviewed=args.external_copies_reviewed)
        else:
            result = plan(args.base, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "check":
            if result["registration"]["status"] == "deleted":
                return 2 if result["file_count"] else 0
            if result["overdue"] or not result["deletion_enabled"]:
                return 2
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        # No filenames from exception strings, private data or raw JSON echoed.
        print(json.dumps({"error": "retention_check_failed", "hint": "核对专用路径、登记、时区、预览令牌、文件变化及停服/外部副本确认；未保证全部删除。"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
