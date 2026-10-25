import atexit
from collections import deque
from contextlib import asynccontextmanager
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response
from memory_app.lab.reports import report_assets, read_report_image
from memory_app.errors import MESSAGES, PROVIDER_CODES
from memory_app.diagnostics import HINTS


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str = Field(min_length=1, max_length=200)
    embeddings: Literal["none", "ollama", "compatible"] = "none"
    allow_remote_embeddings: bool = False
    limit: int = Field(default=10, ge=0, le=10000)
    top_k: int = Field(default=5, ge=1, le=100)
    include_captions: bool = False
    with_images: bool = False
    ocr_languages: Literal["eng", "chi_sim", "chi_sim+eng"] = "eng"

    @model_validator(mode="after")
    def separate_image_modes(self):
        if self.embeddings == "compatible" and not self.allow_remote_embeddings:
            raise ValueError("Confirm paid remote embedding calls first")
        if self.with_images and self.include_captions:
            raise ValueError("Raw images + OCR must not mix dataset-provided captions")
        return self


class ExperimentRunner:
    """One allowlisted child process at a time; never accepts shell commands."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.scenarios = self.root / "data/benchmarks/mem-gallery/data/dialog"
        self.reports = self.root / "data/evaluations/mem-gallery"
        self.image_reports = {"ocr": self.root / "data/evaluations/ocr",
                              "images": self.root / "data/evaluations/images"}
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.closing = False

    def scenario_files(self):
        return {p.name: p for p in self.scenarios.glob("*.json")
                if p.resolve().parent == self.scenarios.resolve()}

    def report_path(self, identity):
        if not identity or "\\" in identity:
            raise HTTPException(404, "Report not found")
        base = self.reports
        for prefix, directory in self.image_reports.items():
            if identity.startswith(prefix + "/"):
                base, identity = directory, identity[len(prefix) + 1:]
                break
        path = (base / identity).resolve()
        if (not path.is_relative_to(base.resolve()) or path.suffix != ".json"
                or not path.is_file()):
            raise HTTPException(404, "Report not found")
        return path

    def snapshot(self):
        with self.lock:
            if self.job is None:
                return None
            return {**self.job, "logs": list(self.job["logs"])}

    def start(self, options):
        if options.scenario not in self.scenario_files():
            raise HTTPException(422, "Choose an available scenario")
        with self.lock:
            if self.closing:
                raise HTTPException(503, "Lab is shutting down")
            if self.job and self.job["status"] == "running":
                raise HTTPException(409, "An experiment is already running")
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "status": "running", "options": options.model_dump(),
                        "logs": deque(maxlen=300), "report_id": None, "exit_code": None}
            thread = threading.Thread(target=self._execute, args=(job_id, options), daemon=True)
            thread.start()
            return self.snapshot()

    def _execute(self, job_id, options):
        output = self.reports / "jobs" / job_id
        command = [sys.executable, "-u", str(self.root / "scripts/evaluate_mem_gallery.py"),
                   "--data", str(self.scenarios / options.scenario),
                   "--embeddings", options.embeddings, "--limit", str(options.limit),
                   "--top-k", str(options.top_k), "--output-dir", str(output)]
        if options.include_captions:
            command.append("--include-captions")
        if options.with_images:
            command.extend(["--with-images", "--ocr-languages", options.ocr_languages, "--image-analysis", "ocr"])
        try:
            with self.lock:
                if self.closing:
                    self.job["status"] = "failed"
                    return
                self.process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                                errors="replace", shell=False,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                process = self.process
            for line in process.stdout:
                with self.lock:
                    self.job["logs"].append(line.rstrip()[:2000])
            code = process.wait()
            matches = list(output.glob("*.json"))
            with self.lock:
                self.job["exit_code"] = code
                self.job["status"] = "completed" if code == 0 and len(matches) == 1 else "failed"
                if self.job["status"] == "completed":
                    self.job["report_id"] = matches[0].relative_to(self.reports).as_posix()
        except Exception as exc:
            with self.lock:
                self.job["status"] = "failed"
                self.job["logs"].append(f"Could not run experiment: {type(exc).__name__}")
        finally:
            with self.lock:
                self.process = None

    def close(self):
        with self.lock:
            self.closing = True
            process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def create_lab(root=None):
    root = Path(root or Path(__file__).resolve().parents[3])
    runner = ExperimentRunner(root)

    @asynccontextmanager
    async def lifespan(app):
        yield
        runner.close()

    app = FastAPI(title="Memory Local Experiment Lab", lifespan=lifespan)
    app.state.runner = runner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    atexit.register(runner.close)

    @app.get("/")
    def home():
        return FileResponse(Path(__file__).with_name("index.html"), media_type="text/html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/scenarios")
    def scenarios():
        return sorted(runner.scenario_files())

    @app.get("/api/reports")
    def reports():
        entries = []
        for path in sorted(runner.reports.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.resolve().is_relative_to(runner.reports.resolve()):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data["metadata"]
                entries.append({"id": path.relative_to(runner.reports).as_posix(),
                                "scenario": meta["scenario"], "created_at": meta["created_at_utc"],
                                "questions": meta["tested_questions"], "top_k": data["top_k"],
                                "captions": meta["include_dataset_captions"],
                                "raw_images": meta.get("raw_images_used", False),
                                "channels": list(data["summary"])})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for kind, directory in runner.image_reports.items():
            for path in directory.rglob("*.json"):
                if not path.resolve().is_relative_to(directory.resolve()):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    if kind == "ocr" and not isinstance(data.get("samples"), list):
                        continue
                    if kind == "images" and "image_artifacts" not in data:
                        continue
                    entries.append({"id": kind + "/" + path.relative_to(directory).as_posix(),
                                    "kind": kind, "scenario": "本地 OCR 图片测试" if kind == "ocr" else "单张图片检查",
                                    "created_at": data.get("created_at_utc"),
                                    "questions": len(data["samples"]) if kind == "ocr" else 1,
                                    "top_k": data.get("top_k", 3), "captions": False,
                                    "channels": ["OCR + BM25"] if kind == "ocr" else ["配置的检索路线"]})
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        entries.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
        return entries

    @app.get("/api/checkpoints")
    def checkpoints():
        entries = []
        for path in runner.reports.rglob("_checkpoints/*/state.json"):
            if not path.resolve().is_relative_to(runner.reports.resolve()):
                continue
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("protocol") != "gallery-checkpoint-v1":
                    continue
                options = state.get("options", {})
                code = (state.get("error") or {}).get("code")
                failure = state.get("error") or {}
                message, hint = MESSAGES.get(code, ("", ""))
                safe_failure = None
                if code in MESSAGES:
                    safe_failure = {"code": code, "message": message, "hint": hint,
                        "component": failure.get("component") if failure.get("component") in {"embedding", "vision", "model", "evaluation", "checkpoint", "storage", "configuration"} else "evaluation",
                        "http_status": failure.get("http_status") if type(failure.get("http_status")) is int else None,
                        "provider_code": failure.get("provider_code") if failure.get("provider_code") in PROVIDER_CODES else None,
                        "retryable": failure.get("retryable") is True}
                    reason = failure.get("diagnostic")
                    if code in {"network_error", "timeout", "storage_error"} and isinstance(reason, str) and reason in HINTS:
                        safe_failure["diagnostic"] = reason
                        safe_failure["hint"] = HINTS[reason] + " 不会自动重试模型请求。"
                entries.append({"id": path.parent.relative_to(runner.reports).as_posix(),
                    "status": state.get("status"), "stage": state.get("stage"),
                    "scenario": options.get("scenario"), "indexed_rounds": state.get("indexed_rounds", 0),
                    "completed_questions": state.get("completed_questions", 0),
                    "total_rounds": state.get("total_rounds"), "total_questions": state.get("total_questions"),
                    "round_id": state.get("round_id"), "question_number": state.get("question_number"),
                    "updated_at_utc": state.get("updated_at_utc"),
                    "error": safe_failure,
                    "resume_path": str(path.parent.resolve())})
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return sorted(entries, key=lambda item: item.get("updated_at_utc") or "", reverse=True)

    @app.get("/api/report")
    def report(id: str):
        try:
            data = json.loads(runner.report_path(id).read_text(encoding="utf-8"))
            if id.startswith("ocr/"):
                data["report_type"] = "ocr"
            elif id.startswith("images/"):
                data["report_type"] = "image-inspection"
            return data
        except (OSError, ValueError):
            raise HTTPException(422, "Report cannot be read")

    @app.get("/api/report-image")
    def report_image(id: str, image: str):
        path = runner.report_path(id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if image not in report_assets(data):
                raise HTTPException(404, "Image not referenced by report")
            raw, mime = read_report_image(path.parent, image)
            return Response(raw, media_type=mime, headers={"X-Content-Type-Options": "nosniff",
                                                          "Cache-Control": "no-store"})
        except (OSError, ValueError, TypeError):
            raise HTTPException(404, "Report image unavailable")

    @app.get("/api/job")
    def job():
        return runner.snapshot()

    @app.post("/api/run", status_code=202)
    def start(options: RunRequest, request: Request):
        # Custom header prevents cross-site form submits; no permissive CORS.
        if request.headers.get("x-memory-lab") != "1":
            raise HTTPException(403, "Use the local experiment page")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin requests are not allowed")
        return runner.start(options)

    return app
