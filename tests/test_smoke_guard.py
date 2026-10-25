import importlib
import json
from pathlib import Path
import sys

import httpx
import pytest


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    return importlib.import_module("smoke")


@pytest.mark.parametrize("mode,vision",[("compatible",False),("none",True),("ollama",False)])
def test_smoke_requires_explicit_model_permission(smoke,monkeypatch,tmp_path,capsys,mode,vision):
    env=tmp_path/".env"
    env.write_text("MEMORY_API_KEY=test-file-only\n")
    monkeypatch.setenv("MEMORY_API_KEY","test-env-only")
    monkeypatch.setattr(sys,"argv",["smoke.py","--env-file",str(env)])
    original=httpx.Client
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.method=="GET" and request.url.path=="/health"
        assert request.headers["X-Api-Key"]=="test-env-only"
        return httpx.Response(200,json={"embeddings":mode,"image_analysis":vision})
    monkeypatch.setattr(smoke.httpx,"Client",lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
    with pytest.raises(SystemExit) as caught:smoke.run()
    assert caught.value.code==2 and len(requests)==1
    output=capsys.readouterr()
    assert "test-env-only" not in output.err and "test-file-only" not in output.err


def test_model_free_smoke_can_use_key_from_private_file(smoke,monkeypatch,tmp_path,capsys):
    env=tmp_path/".env"
    env.write_text("MEMORY_API_KEY=test-file-only\n")
    monkeypatch.delenv("MEMORY_API_KEY",raising=False)
    monkeypatch.setattr(sys,"argv",["smoke.py","--env-file",str(env)])
    original=httpx.Client
    added=[]
    def handler(request):
        assert request.headers["X-Api-Key"]=="test-file-only"
        if request.method=="GET":return httpx.Response(200,json={"embeddings":"none","image_analysis":False})
        payload=json.loads(request.content)
        if request.url.path=="/add":
            added.append(payload)
            return httpx.Response(200,json={"success":True,**{k:payload[k] for k in ("request_id","user_id","session_id")}})
        results=[] if payload["user_id"].endswith("-other") else [{"id":"test","content":added[0]["messages"][0]["content"]}]
        return httpx.Response(200,json={"data":results})
    monkeypatch.setattr(smoke.httpx,"Client",lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
    smoke.run()
    assert len(added)==2 and added[0]==added[1]
    assert "test-file-only" not in capsys.readouterr().out
