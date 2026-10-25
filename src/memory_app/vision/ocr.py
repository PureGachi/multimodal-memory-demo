import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image
from memory_app.memory.errors import ModelUnavailable
from memory_app.vision.base import ImageAnalysis


class TesseractAnalyzer:
    """Optional CPU OCR. Tesseract and language packs are installed separately."""

    def __init__(self, executable="tesseract", languages="eng", revision="1"):
        self.executable, self.languages = executable, languages
        # Conda's Windows executable may not resolve share/tessdata when the
        # environment is not activated. Respect an explicit directory first.
        self.tessdata_dir = os.getenv("TESSDATA_PREFIX") or None
        resolved = Path(shutil.which(executable) or executable)
        if self.tessdata_dir is None and resolved.is_absolute():
            candidate = resolved.parent.parent.parent / "share/tessdata"
            if candidate.is_dir():
                self.tessdata_dir = str(candidate)
        try:
            result = subprocess.run([executable, "--version"], capture_output=True,
                                    timeout=10, check=True, creationflags=self.flags())
            engine = result.stdout.decode("utf-8", errors="replace").splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError) as exc:
            raise ModelUnavailable("Tesseract is not available; install it or disable OCR") from exc
        identity = json.dumps([executable, engine, languages, revision, self.tessdata_dir, "psm3-png-v1"])
        self.version = "ocr:" + hashlib.sha256(identity.encode()).hexdigest()
        self.provenance = {"type": "ocr", "engine": engine, "languages": languages,
                           "revision": revision, "preprocessing": "RGB PNG; psm3",
                           "tessdata_dir": self.tessdata_dir}

    @staticmethod
    def flags():
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def analyze(self, raw, mime):
        try:
            with tempfile.TemporaryDirectory(prefix="memory-ocr-") as folder:
                target = Path(folder) / "input.png"
                with Image.open(io.BytesIO(raw)) as image:
                    image.convert("RGB").save(target)
                data_args = ["--tessdata-dir", self.tessdata_dir] if self.tessdata_dir else []
                result = subprocess.run([self.executable, str(target), "stdout", *data_args, "-l",
                                         self.languages, "--psm", "3"],
                                        capture_output=True, timeout=60, check=True,
                                        creationflags=self.flags())
                return ImageAnalysis(visible_text=result.stdout.decode("utf-8").strip())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ModelUnavailable("OCR failed; check engine and language packs") from exc
