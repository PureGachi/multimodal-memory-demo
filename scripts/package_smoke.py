"""Build a wheel offline, install it in an isolated prefix, run real HTTP tests."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

from memory_app.errors import error_feedback
from memory_app.evaluation.checkpoint import atomic_json
from deployment_smoke import run_probe


def main():
    workspace = Path(__file__).resolve().parents[1]
    output = workspace / "data/evaluations/deployment"
    output.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-package-" + uuid.uuid4().hex[:8]
    report = {"created_at_utc":datetime.now(timezone.utc).isoformat(),"status":"running",
              "kind":"offline-wheel-installation","remote_model_calls":0,
              "docker_available":shutil.which("docker") is not None,
              "dependency_scope":"isolated venv prefix with host's installed dependencies; no network installation"}
    phase = "build-wheel"
    env = {**os.environ,"PIP_DISABLE_PIP_VERSION_CHECK":"1","PYTHONUTF8":"1"}
    def command(args, *, cwd):
        subprocess.run(args, cwd=cwd, env=env, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0), timeout=90)
    try:
        with tempfile.TemporaryDirectory(prefix="memory-package-probe-") as folder:
            root = Path(folder)
            command([sys.executable,"-m","pip","wheel","--no-index","--no-deps","--no-build-isolation",
                "--wheel-dir",str(root/"wheels"),str(workspace)],cwd=root)
            wheels=list((root/"wheels").glob("*.whl"))
            if len(wheels)!=1:raise ValueError("Expected a single built wheel")
            wheel=wheels[0]
            with zipfile.ZipFile(wheel) as archive:
                names=archive.namelist()
                if any(not (n.startswith("memory_app/") or n.startswith("memory_demo-"))
                       or ".env" in Path(n).parts or n.endswith((".sqlite3",".json",".log")) for n in names):
                    raise ValueError("Unexpected artifact in wheel")
            report["wheel_entries_checked"]=len(names)
            phase="install-wheel"
            command([sys.executable,"-m","venv","--system-site-packages","--without-pip",str(root/"venv")],cwd=root)
            python=root/"venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
            command([str(python),"-m","pip","install","--no-index","--no-deps","--ignore-installed",str(wheel)],cwd=root)
            command([str(python),"-c","import memory_app,sys; from pathlib import Path; assert Path(memory_app.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())"],cwd=root)
            phase="installed-http-probe"
            report["http_probe"]=run_probe(python=python,installed=True)
            if report["http_probe"]["status"]!="passed":raise RuntimeError("Installed HTTP probe failed")
            artifacts=output/"artifacts"/name
            artifacts.mkdir(parents=True,exist_ok=False)
            artifact=artifacts/wheel.name
            shutil.copyfile(wheel,artifact)
            report.update(status="passed",wheel=str(artifact.relative_to(workspace)),
                          wheel_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    except Exception as exc:
        report.update(status="failed",phase=phase,error=error_feedback(exc))
    target=output/(name+".json")
    atomic_json(target,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print("Report:",target)
    if report["status"]!="passed":raise SystemExit(1)


if __name__=="__main__":main()
