import importlib.util
import json
from pathlib import Path
import socket
import ssl

import httpx
import httpx2
import pytest

from memory_app.diagnostics import classify_failure
from memory_app.errors import error_feedback
from memory_app.memory.errors import ModelUnavailable


@pytest.mark.parametrize("inner,reason", [
    (socket.gaierror(-2, "sk-secret private hostname"), "dns_failure"),
    (ssl.SSLCertVerificationError(1, "private certificate"), "tls_certificate"),
    (ConnectionResetError("secret body"), "connection_reset"),
    (PermissionError("private path"), "permission_denied"),
    (httpx.RemoteProtocolError("private response"), "remote_protocol"),
    (httpx2.RemoteProtocolError("private response"), "remote_protocol"),
    (httpx2.ReadTimeout("secret timeout"), "read_timeout"),
])
def test_classification_uses_static_categories_only(inner, reason):
    outer = RuntimeError("sk-secret")
    outer.__cause__ = inner
    assert classify_failure(outer) == reason
    exc = ModelUnavailable("secret", code="network_error", diagnostic=reason)
    result = error_feedback(exc)
    assert result["diagnostic"] == reason
    assert "secret" not in json.dumps(result)


def test_unknown_diagnostic_cannot_leak_and_cause_cycles_terminate():
    exc = ModelUnavailable("secret", code="network_error", diagnostic="sk-secret")
    exc.__cause__ = exc
    assert classify_failure(exc) == "unknown"
    assert "sk-secret" not in json.dumps(error_feedback(exc))
    assert error_feedback(PermissionError("private path"))["diagnostic"] == "permission_denied"


def test_suppressed_transport_context_can_still_identify_tls_safely():
    wrapper=httpx.ConnectError("private transport text")
    wrapper.__context__=ssl.SSLCertVerificationError(1,"secret certificate text")
    wrapper.__suppress_context__=True
    assert classify_failure(wrapper)=="tls_certificate"


def test_sdk_connection_error_preserves_classification_without_retry(monkeypatch):
    from test_vision import mock_vision_transport
    from memory_app.llms.compatible import CompatibleVisionClient
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx2.RemoteProtocolError("sk-secret private body", request=request)
    mock_vision_transport(monkeypatch, handler)
    client = CompatibleVisionClient("https://example.test/v1", "sk-secret", "gpt-4o-mini")
    with pytest.raises(ModelUnavailable) as caught:
        client.complete("private prompt", b"private image", "image/png")
    assert len(calls) == 1
    feedback = error_feedback(caught.value)
    assert feedback["diagnostic"] == "remote_protocol"
    assert "private" not in json.dumps(feedback) and "sk-secret" not in json.dumps(feedback)


def test_probe_never_sends_credentials_or_follows_redirects(monkeypatch):
    spec = importlib.util.spec_from_file_location("network_probe", Path(__file__).resolve().parents[1] / "scripts/network_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        assert request.content == b"{}"
        return httpx.Response(401)
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx, "Client", client)
    assert module.probe("https://example.test/v1")["http_status"] == 401
    assert len(calls) == 1
    with pytest.raises(ValueError):
        module.probe("https://user:secret@example.test/v1")
