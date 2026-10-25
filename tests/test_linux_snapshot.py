import importlib
from pathlib import Path


def test_linux_snapshot_excludes_live_private_roots_and_preserves_allowed_examples(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("linux_tests")
    workspace, target = tmp_path / "workspace", tmp_path / "snapshot"
    workspace.mkdir()
    target.mkdir()
    for name in module.FOLDERS:
        (workspace / name).mkdir()
    for name in module.FILES:
        (workspace / name).write_text("public fixture", encoding="utf-8")
    (workspace / ".env").write_text("private live fixture", encoding="utf-8")
    (workspace / "data").mkdir()
    (workspace / "data/keep").write_text("private data fixture", encoding="utf-8")
    fingerprint = module.snapshot(workspace, target)
    assert len(fingerprint) == 64
    assert (target / ".env.example").is_file()
    assert not (target / ".env").exists() and not (target / "data").exists()
    assert (workspace / "data/keep").is_file()
