"""Unit tests for Docker safety guards; never starts a Docker daemon or models."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("docker_smoke")


def test_cleanup_refuses_resources_with_other_ownership(probe, monkeypatch):
    commands = []
    def fake(*args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps([{"Config": {"Labels": {"memory-demo.probe": "other"}},
                                                              "Labels": {"memory-demo.probe": "other"}}]).encode())
    monkeypatch.setattr(probe, "docker", fake)
    errors = probe.DockerService("fixture").cleanup()
    assert len(errors) == 3
    assert not any("rm" in command or "stop" in command for command in commands)


def test_failed_inspect_is_not_treated_as_confirmed_removal(probe, monkeypatch):
    def fake(*args, **kwargs):
        if "inspect" in args:
            return SimpleNamespace(returncode=1, stdout=b"")
        raise RuntimeError("daemon unavailable")
    monkeypatch.setattr(probe, "docker", fake)
    assert len(probe.DockerService("fixture").cleanup()) == 3


def test_missing_resource_is_only_accepted_after_successful_listing(probe, monkeypatch):
    def fake(*args, **kwargs):
        return SimpleNamespace(returncode=1 if "inspect" in args else 0, stdout=b"")
    monkeypatch.setattr(probe, "docker", fake)
    assert probe.DockerService("fixture").cleanup() == []


def test_container_probe_redacts_original_exception(probe, monkeypatch):
    with pytest.raises(ValueError):
        probe.verify_image("--fixture-option")
    monkeypatch.setattr(probe, "verify_image", lambda image: (_ for _ in ()).throw(RuntimeError("private fixture detail")))
    report = probe.run_probe("fixture")
    assert report["status"] == "failed" and report["diagnostic"] == "RuntimeError"
    assert "private fixture detail" not in json.dumps(report)


def test_probe_fixture_environment_never_forwards_host_model_keys(probe, monkeypatch):
    monkeypatch.setenv("VISION_API_KEY", "private-host-fixture")
    service = probe.DockerService("fixture")
    captured = []
    def fake(*args, **kwargs):
        captured.append(args)
        if args[0] == "run":
            raise RuntimeError("end fixture before startup")
        return SimpleNamespace(returncode=1, stdout=b"")
    monkeypatch.setattr(probe, "docker", fake)
    monkeypatch.setattr(service, "remove_container", lambda: None)
    with pytest.raises(RuntimeError):
        service.start()
    assert "private-host-fixture" not in json.dumps(captured)
    assert "--publish" not in captured[0]
