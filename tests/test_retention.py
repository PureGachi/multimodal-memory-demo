from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest

from memory_app.governance.retention import initialize, complete, plan, apply, scope, MARKER, RECEIPT


def finished(base, run="test-run"):
    initialize(base, run)
    at = datetime.now(timezone.utc) - timedelta(days=1)
    complete(base, run, at.isoformat())
    return base / run


def test_registration_has_no_invented_completion_or_deadline(tmp_path):
    base = tmp_path / "official"
    initialize(base, "run1")
    preview = plan(base, "run1")
    assert preview["registration"]["delete_by"] is None
    assert not preview["deletion_enabled"]
    with pytest.raises(ValueError):
        apply(base, "run1", preview["confirm_token"], service_stopped=True, external_copies_reviewed=True)


def test_preview_and_explicit_apply_only_remove_registered_fixture(tmp_path):
    base = tmp_path / "official"
    root = finished(base)
    (root / "runtime/media").mkdir(parents=True)
    (root / "runtime/memory.sqlite3").write_bytes(b"fixture database")
    (root / "runtime/media/picture").write_bytes(b"fixture image")
    other = finished(base, "other-run")
    (other / "keep").write_bytes(b"other fixture")
    unrelated = tmp_path / "mem-gallery"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"public dataset fixture")
    preview = plan(base, "test-run")
    assert preview["file_count"] == 2
    assert (root / "runtime/memory.sqlite3").exists()  # preview does not delete
    with pytest.raises(ValueError):
        apply(base, "test-run", preview["confirm_token"])
    receipt = apply(base, "test-run", preview["confirm_token"], service_stopped=True, external_copies_reviewed=True)
    assert receipt["file_count"] == 2 and (root / MARKER).exists() and (root / RECEIPT).exists()
    assert (other / "keep").exists() and (unrelated / "keep").exists()
    assert plan(base, "test-run")["registration"]["status"] == "deleted"


def test_changed_inventory_invalidates_confirmation(tmp_path):
    base = tmp_path / "official"
    root = finished(base)
    (root / "record").write_bytes(b"before")
    token = plan(base, "test-run")["confirm_token"]
    (root / "record").write_bytes(b"after")
    with pytest.raises(ValueError):
        apply(base, "test-run", token, service_stopped=True, external_copies_reviewed=True)
    assert (root / "record").read_bytes() == b"after"


@pytest.mark.parametrize("run", ["../x", ".", "..", "a/b", "a\\b", "", "/", "C:"])
def test_scope_cannot_target_broad_or_escaped_paths(tmp_path, run):
    with pytest.raises(ValueError):
        scope(tmp_path / "official", run)


def test_refuses_broad_parent_and_adoption_of_nonempty_data(tmp_path):
    with pytest.raises(ValueError):
        initialize(tmp_path, "run")
    root = tmp_path / "official/run"
    root.mkdir(parents=True)
    (root / "important").write_bytes(b"do not adopt")
    with pytest.raises(ValueError):
        initialize(tmp_path / "official", "run")
    assert not (root / MARKER).exists()


def test_completion_needs_timezone_is_once_only_and_exactly_30_days(tmp_path):
    base = tmp_path / "official"
    initialize(base, "run")
    with pytest.raises(ValueError):
        complete(base, "run", "2026-01-01T00:00:00")
    with pytest.raises(ValueError):
        complete(base, "run", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    now = datetime.now(timezone.utc) - timedelta(days=31)
    result = complete(base, "run", now.isoformat())
    assert datetime.fromisoformat(result["delete_by"]) - now == timedelta(days=30)
    assert plan(base, "run")["overdue"]
    with pytest.raises(ValueError):
        complete(base, "run", datetime.now(timezone.utc).isoformat())


def test_links_and_junctions_are_rejected(tmp_path):
    base = tmp_path / "official"
    root = finished(base)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (root / "redirect").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit symlink fixture")
    with pytest.raises(ValueError):
        plan(base, "test-run")
    assert target.exists()


def test_reserved_metadata_directory_cannot_hide_data(tmp_path):
    base = tmp_path / "official"
    root = finished(base)
    (root / RECEIPT).mkdir()
    with pytest.raises(ValueError):
        plan(base, "test-run")


def test_windows_reparse_flag_is_rejected_without_link_privileges():
    from types import SimpleNamespace
    from memory_app.governance.retention import no_links
    class Junction:
        parents = ()
        def is_symlink(self): return False
        def exists(self): return True
        def lstat(self): return SimpleNamespace(st_file_attributes=1024)
    with pytest.raises(ValueError):
        no_links(Junction())


def test_hardlinked_copies_require_separate_review(tmp_path):
    import os
    base = tmp_path / "official"
    root = finished(base)
    original = tmp_path / "outside-file"
    original.write_bytes(b"fixture only")
    try:
        os.link(original, root / "hardlink")
    except OSError:
        pytest.skip("Filesystem does not support hard links")
    with pytest.raises(ValueError):
        plan(base, "test-run")
    assert original.read_bytes() == b"fixture only"
