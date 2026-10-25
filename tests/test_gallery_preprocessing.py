import hashlib
import importlib
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from memory_app.evaluation import gallery_media
from test_mem_gallery_evaluation import evaluation, multimodal_scene


def jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(stream, format="JPEG")
    return stream.getvalue()


def test_explicit_oversize_copy_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "original.jpg"
    original = jpeg() + b"x" * 2000
    path.write_bytes(original)
    monkeypatch.setattr(gallery_media, "MAX_IMAGE", 1000)
    with pytest.raises(ValueError, match="compress-oversize"):
        gallery_media.prepared_image_uri(path)
    uri, raw, info = gallery_media.prepared_image_uri(path, compress_oversize=True)
    assert path.read_bytes() == original
    assert info["transformed"] is True and info["lossy"] is True
    assert info["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert info["output_bytes"] == len(raw) <= 1000
    assert info["source_dimensions"] == info["output_dimensions"] == [32, 32]
    assert uri.startswith("data:image/jpeg;base64,")
    assert gallery_media.prepared_image_uri(path, compress_oversize=True)[1] == raw


def test_small_image_bytes_unchanged(tmp_path):
    path = tmp_path / "original.jpg"
    path.write_bytes(jpeg())
    _, raw, info = gallery_media.prepared_image_uri(path, compress_oversize=True)
    assert raw == path.read_bytes() and info["transformed"] is False


def test_history_and_query_compression_share_policy(multimodal_scene, tmp_path, monkeypatch):
    from memory_app.vision.base import ImageAnalysis
    path = tmp_path / "data/image/scene/one.jpg"
    original = path.read_bytes() + b"x" * 2000
    path.write_bytes(original)
    data = json.loads(multimodal_scene.read_text())
    data["human-annotated QAs"][0]["question_image"] = "../image/scene/one.jpg"
    multimodal_scene.write_text(json.dumps(data))
    monkeypatch.setattr(gallery_media, "MAX_IMAGE", 1000)
    rounds, cases = evaluation.load_scenario(multimodal_scene, with_images=True,
        assets_dir=tmp_path / "assets", compress_oversize=True)
    history, query = rounds[0]["images"][0], cases[0]["query_images"][0]
    assert history["sha256"] == query["sha256"]
    assert history["preprocessing"] == query["preprocessing"]
    class Vision:
        version = "test-jpeg"
        def analyze(self, raw, mime):
            assert mime == "image/jpeg"
            return ImageAnalysis(visible_text="M42")
    report = evaluation.evaluate(rounds, cases, vision=Vision())
    assert report["history_image_preprocessing"][0]["transformed"] is True
    assert report["details"][0]["query_images"][0]["preprocessing"]["transformed"] is True
    assert "base64," not in json.dumps(report)
    assert path.read_bytes() == original


@pytest.fixture
def downloader(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("download_mem_gallery_images")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    return module


def test_download_retries_partial_body_without_publishing_it(downloader, tmp_path):
    raw = jpeg()
    calls = []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield raw[:100]
            raise httpx.RemoteProtocolError("incomplete")
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Interrupted()) if len(calls) == 1 else httpx.Response(200, content=raw)
    target = tmp_path / "photo.jpg"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader.download_file(client, "https://example.test/photo", target, expected=hashlib.sha256(raw).hexdigest())
    assert len(calls) == 2 and target.read_bytes() == raw
    assert not list(tmp_path.glob('.download-*'))


def test_bad_checksum_and_failed_retries_never_create_original(downloader, tmp_path):
    target = tmp_path / "photo.jpg"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=jpeg()))) as client:
        with pytest.raises(ValueError, match="checksum"):
            downloader.download_file(client, "https://example.test/photo", target, expected="bad")
    assert not target.exists()
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("interrupted")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            downloader.download_file(client, "https://example.test/photo", target)
    assert len(calls) == 3 and not target.exists()


def test_large_original_flag_and_existing_file_preserved(downloader, tmp_path, monkeypatch):
    raw = jpeg() + b"x" * 2000
    monkeypatch.setattr(downloader, "MAX_IMAGE", 1000)
    target = tmp_path / "photo.jpg"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))) as client:
        with pytest.raises(ValueError):
            downloader.download_file(client, "https://example.test/photo", target)
        assert not target.exists()
        downloader.download_file(client, "https://example.test/photo", target, allow_large=True)
        assert target.read_bytes() == raw
        with pytest.raises(ValueError, match="checksum"):
            downloader.download_file(client, "https://example.test/photo", target, allow_large=True, expected="bad")
    assert target.read_bytes() == raw
