"""Run the regression suite in Linux using a sanitized, read-only source snapshot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from memory_app.evaluation.checkpoint import atomic_json
from memory_app.governance.retention import no_links

FOLDERS = ("src", "scripts", "tests", "docs", "deploy")
FILES = ("pyproject.toml", "requirements-lock.txt", "README.md", "LICENSE", "Dockerfile",
         ".dockerignore", ".gitignore", ".env.example", ".env.competition.example")


def snapshot(workspace, target):
    manifest = []
    def copy_file(source, destination):
        source = Path(source)
        no_links(source)
        if source.name == ".env" or (source.name.startswith(".env.") and source.name not in FILES):
            raise ValueError("Private env file cannot enter snapshot")
        raw = source.read_bytes()
        manifest.append({"path": source.relative_to(workspace).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()})
        return shutil.copyfile(source, destination)
    for folder in FOLDERS:
        no_links(workspace / folder)
        shutil.copytree(workspace / folder, target / folder, copy_function=copy_file,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".env", ".env.deploy"))
    for name in FILES:
        copy_file(workspace / name, target / name)
    if any((target / name).exists() for name in (".env", "data", ".conda", ".git")):
        raise ValueError("Unexpected private root in snapshot")
    return hashlib.sha256(json.dumps(sorted(manifest, key=lambda x: x["path"]), sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already-built development test image with git and node")
    args = parser.parse_args()
    if not args.image or args.image.startswith("-"):
        parser.error("--image must be a local image reference")
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
                              capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    workspace = Path(__file__).resolve().parents[1]
    name = "memory-linux-tests-" + uuid.uuid4().hex
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "kind": "linux-source-regression-v1",
              "status": "running", "remote_model_calls": 0, "network": "none", "test_image": args.image, "test_image_id": image_id,
              "scope": "read-only sanitized source snapshot with image's Linux dependencies; not official evaluation"}
    with tempfile.TemporaryDirectory(prefix="memory-linux-tests-") as folder:
        target = Path(folder).resolve()
        report["snapshot_sha256"] = snapshot(workspace, target)
        command = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network=none",
                   "--label", "memory-demo.linux-tests=" + name,
                   "--mount", "type=bind,source=" + str(target) + ",target=/verification,readonly",
                   "--workdir", "/verification", "--memory", "2g", "--cpus", "2",
                   "--env", "PYTHONPATH=/verification/src", "--env", "MEMORY_EMBEDDINGS=none",
                   "--env", "MEMORY_VISION=none", "--env", "MEMORY_OCR=none",
                   image_id, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest"]
        # No host environment keys are forwarded; no .env/data is mounted.
        try:
            result = subprocess.run(command, capture_output=True, timeout=240)
            output = result.stdout.decode("utf-8", errors="replace")
            report["status"] = "passed" if result.returncode == 0 else "failed"
            report["exit_code"] = result.returncode
            # Output belongs exclusively to synthetic tests, not private live requests.
            report["test_output"] = output[-18000:]
        except (OSError, subprocess.SubprocessError):
            report.update(status="failed", error="linux_test_process_failed")
        finally:
            try:
                check = subprocess.run(["docker", "ps", "-a", "--filter", "label=memory-demo.linux-tests=" + name,
                                        "--format", "{{.Names}}"], capture_output=True, check=True, timeout=20)
                if name in check.stdout.decode().splitlines():
                    subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=True, timeout=30)
                report["owned_container_removed"] = True
            except (OSError, subprocess.SubprocessError):
                report.update(status="failed", owned_container_removed=False)
    report["host_test_snapshot_removed"] = True
    destination = workspace / "data/evaluations/deployment" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-linux-tests-" + uuid.uuid4().hex[:8] + ".json")
    atomic_json(destination, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Report:", destination)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
