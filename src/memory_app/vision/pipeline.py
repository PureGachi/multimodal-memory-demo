import hashlib
import json
from contextlib import ExitStack
from dataclasses import asdict
from memory_app.memory.errors import ModelUnavailable
from memory_app.vision.base import ImageAnalysis


class ImagePipeline:
    def __init__(self, analyzers):
        self.analyzers = analyzers
        self.supports_context = any(getattr(a, "supports_context", False) for a in analyzers)
        self.provenance = [getattr(analyzer, "provenance", {"version": analyzer.version})
                           for analyzer in analyzers]
        self.version = "pipeline:" + hashlib.sha256(json.dumps(
            [analyzer.version for analyzer in analyzers]).encode()).hexdigest()

    def close(self):
        with ExitStack() as stack:
            for analyzer in self.analyzers:
                close = getattr(analyzer, "close", None)
                if close is not None:
                    stack.callback(close)

    def analyze(self, raw, mime):
        return self._analyze(raw, mime, None)

    def analyze_with_context(self, raw, mime, context):
        return self._analyze(raw, mime, context)

    def _analyze(self, raw, mime, context):
        results = [analyzer.analyze_with_context(raw, mime, context)
                   if context and getattr(analyzer, "supports_context", False)
                   else analyzer.analyze(raw, mime) for analyzer in self.analyzers]
        # Bound the combined result too; blank OCR on a text-free image is valid.
        try:
            return ImageAnalysis(
                description="\n".join(r.description for r in results if r.description),
                visible_text="\n".join(r.visible_text for r in results if r.visible_text),
                contextual_description="\n".join(r.contextual_description for r in results if r.contextual_description),
            )
        except ValueError as exc:
            raise ModelUnavailable("Combined image analysis exceeds output limits") from exc


def analysis_version(vision, context=None):
    if context and getattr(vision, "supports_context", False):
        fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return vision.version + ":context-v1:" + fingerprint
    return vision.version


def analyze_cached(store, vision, user_id, key, raw, mime, cache, *, context=None):
    """Cache includes user scope, image bytes and configured pipeline version."""
    version = analysis_version(vision, context)
    identity = (user_id, key, version)
    if identity not in cache:
        value = store.get_image_analysis(user_id, key, version)
        if value is None:
            result = (vision.analyze_with_context(raw, mime, context)
                      if context and getattr(vision, "supports_context", False)
                      else vision.analyze(raw, mime))
            value = asdict(result)
        cache[identity] = value
    return cache[identity]
