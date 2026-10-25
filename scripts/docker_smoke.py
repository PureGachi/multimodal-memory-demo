"""Real Linux-container acceptance with isolated synthetic data and no model calls."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

import httpx
from _http_checks import exercise_http
from memory_app.evaluation.checkpoint import atomic_json

LABEL = "memory-demo.probe"


def docker(*args, optional=False, payload=None):
    result = subprocess.run(["docker", *args], input=payload, capture_output=True, timeout=90)
    if result.returncode and not optional:
        raise RuntimeError("Docker operation failed; raw output suppressed")
    return result


class DockerExecTransport(httpx.BaseTransport):
    """Real HTTP inside an isolated container, without publishing an ingress port."""
    def __init__(self, name):
        self.name = name

    def handle_request(self, request):
        code = """import sys,json,base64,httpx
p=json.load(sys.stdin)
with httpx.Client(timeout=20,trust_env=False) as c:
 r=c.request(p['method'],'http://127.0.0.1:8000'+p['path'],headers=p['headers'],content=base64.b64decode(p['body']))
 print(json.dumps({'status':r.status_code,'headers':dict(r.headers),'body':base64.b64encode(r.content).decode()}))
"""
        value = {"method": request.method, "path": request.url.raw_path.decode(),
                 "headers": dict(request.headers), "body": base64.b64encode(request.content).decode()}
        result = docker("exec", "--interactive", self.name, "python", "-c", code,
                        payload=json.dumps(value).encode())
        response = json.loads(result.stdout)
        return httpx.Response(response["status"], headers=response["headers"], content=base64.b64decode(response["body"]))


class DockerService:
    def __init__(self, image, *, internal=True):
        self.nonce = uuid.uuid4().hex
        self.name = "memory-probe-" + self.nonce
        self.volume = self.name + "-data"
        self.network = self.name + "-net"
        self.image, self.api_key = image, uuid.uuid4().hex
        self.internal = internal
        self.port = None
        self.container_ids = []

    def inspect(self, kind, name):
        result = docker(kind, "inspect", name, optional=True)
        if result.returncode:
            # Do not confuse daemon/permission failure with successful removal.
            fields = ["container", "ls", "-a", "--format", "{{.Names}}"] if kind == "container" else [kind, "ls", "--format", "{{.Name}}"]
            names = docker(*fields).stdout.decode().splitlines()
            if name in names:
                raise RuntimeError("Owned resource exists but could not be inspected")
            return None
        value = json.loads(result.stdout)[0]
        labels = value.get("Config", {}).get("Labels") if kind == "container" else value.get("Labels")
        if (labels or {}).get(LABEL) != self.nonce:
            raise RuntimeError("Resource ownership mismatch; refusing mutation")
        return value

    def prepare(self):
        docker("volume", "create", "--label", LABEL + "=" + self.nonce, self.volume)
        docker("network", "create", *(["--internal"] if self.internal else []), "--label", LABEL + "=" + self.nonce, self.network)
        assert self.inspect("volume", self.volume) and self.inspect("network", self.network)

    def remove_container(self):
        value = self.inspect("container", self.name)
        if value is not None:
            if value["State"]["Running"]:
                docker("stop", "-t", "10", self.name)
            docker("rm", self.name)

    def start(self, *, competition=False):
        self.remove_container()
        env = {"MEMORY_PROFILE": "local", "MEMORY_EMBEDDINGS": "none", "MEMORY_VISION": "none",
               "MEMORY_OCR": "none", "MEMORY_API_KEY": self.api_key,
               "MEMORY_DATA_DIR": "/data/probe-runtime", "MEMORY_MAX_REQUEST_BYTES": "1048576",
               "MEMORY_MAX_INFLIGHT": "2", "MEMORY_BODY_TIMEOUT_SECONDS": "30"}
        if competition:
            # Fake credentials + an internal network. Only health/validation/top_k=0
            # are tested in this mode, never image analysis or embedding generation.
            env.update(MEMORY_PROFILE="competition", MEMORY_EMBEDDINGS="compatible", MEMORY_VISION="compatible",
                       EMBEDDING_BASE_URL="https://model.invalid/v1", EMBEDDING_API_KEY="probe-not-real",
                       EMBEDDING_MODEL="text-embedding-v4", EMBEDDING_DIMENSIONS="1024",
                       VISION_BASE_URL="https://model.invalid/v1", VISION_API_KEY="probe-not-real",
                       VISION_MODEL="gpt-4o-mini")
        publish = "127.0.0.1:" + (str(self.port) if self.port else "") + ":8000"
        command = ["run", "--detach", "--pull=never", "--name", self.name,
                   "--label", LABEL + "=" + self.nonce, "--network", self.network,
                   "--mount", "type=volume,source=" + self.volume + ",target=/data",
                   "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges"]
        if not self.internal:
            command.extend(["--publish", publish])
        for key, value in env.items():
            command.extend(["--env", key + "=" + value])
        command.append(self.image)
        identity = docker(*command).stdout.decode().strip()
        self.container_ids.append(identity)
        value = self.inspect("container", self.name)
        if not self.internal:
            binding = value["NetworkSettings"]["Ports"]["8000/tcp"][0]
            if binding["HostIp"] != "127.0.0.1":
                raise RuntimeError("Probe must bind loopback only")
            self.port = int(binding["HostPort"])
        elif value["HostConfig"].get("PortBindings"):
            raise RuntimeError("Internal probe must not publish ports")
        deadline = time.monotonic() + 40
        with self.client() as client:
            while time.monotonic() < deadline:
                value = self.inspect("container", self.name)
                if not value["State"]["Running"]:
                    raise RuntimeError("Container exited during startup")
                try:
                    if client.get(self.url + "/health").status_code == 200:
                        return
                except (httpx.HTTPError, RuntimeError):
                    pass
                time.sleep(.3)
        raise RuntimeError("Container startup timed out")

    @property
    def url(self):
        return "http://127.0.0.1:" + str(8000 if self.internal else self.port)

    def client(self):
        return httpx.Client(base_url=self.url, timeout=20, trust_env=False,
                            transport=DockerExecTransport(self.name) if self.internal else None)

    def cleanup(self):
        errors = []
        try:
            self.remove_container()
        except Exception:
            errors.append("probe-container")
        for kind, name in (("volume", self.volume), ("network", self.network)):
            try:
                if self.inspect(kind, name) is not None:
                    docker(kind, "rm", name)
            except Exception:
                errors.append("probe-" + kind)
        return errors


def verify_image(image):
    if not image or image.startswith("-"):
        raise ValueError("Expected a local image reference, not options")
    value = json.loads(docker("image", "inspect", image).stdout)[0]
    if value["Os"] != "linux" or value["Architecture"] != "amd64" or value["Config"]["User"] in {"", "0", "root"}:
        raise RuntimeError("Expected non-root Linux amd64 image")
    code = """import json,os,pathlib,memory_app,importlib.metadata
root=pathlib.Path('/app')
assert os.getuid()!=0
assert not any((root/n).exists() for n in ('.env','.env.deploy','data','.conda','scripts','tests'))
assert (root/'LICENSE').is_file()
assert '/usr/local/lib/' in memory_app.__file__
assert importlib.metadata.version('memory-demo')==memory_app.__version__
print(json.dumps({'uid':os.getuid(),'version':memory_app.__version__,'installed_package':True,'private_paths_absent':True,'license_present':True}))
"""
    result = docker("run", "--rm", "--pull=never", "--network=none", value["Id"], "python", "-c", code)
    return {"id": value["Id"], "os": value["Os"], "architecture": value["Architecture"],
            "size_bytes": value["Size"], **json.loads(result.stdout)}


def run_probe(image):
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "kind": "docker-local-acceptance-v1",
              "status": "running", "remote_model_calls": 0, "checks": [],
              "scope": "synthetic fixtures; not official Smoke, load testing or remote model quality"}
    services, phase = [], "inspect-image"
    started = time.monotonic()
    try:
        report["image"] = verify_image(image)
        report["checks"].append("nonroot-installed-package-private-paths-license")
        phase = "create-isolated-resources"
        service = DockerService(report["image"]["id"])
        services.append(service)
        report["owned_resources"] = {"container": service.name, "volume": service.volume, "network": service.network}
        service.prepare()
        phase = "start-local-container"
        service.start()
        report["checks"].append("internal-network-no-published-port-startup")
        with service.client() as client:
            health = client.get("/health").json()
            assert health["embeddings"] == "none" and health["image_analysis"] is False
            phase = "shared-http-contract"
            exercise_http(client, service.api_key, service.start, report["checks"])
        assert len(service.container_ids) == 2 and len(set(service.container_ids)) == 2
        report["checks"].append("recreated-container-same-volume-durability")
        phase = "linux-dependency-integrity"
        docker("exec", service.name, "python", "-m", "pip", "check")
        report["checks"].append(phase)
        phase = "docker-healthcheck"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            status = service.inspect("container", service.name)["State"]["Health"]["Status"]
            if status == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Image healthcheck did not become healthy")
        report["checks"].append(phase)
        phase = "competition-profile-without-inference"
        service.start(competition=True)
        with service.client() as client:
            assert client.get("/health").json()["embeddings"] == "compatible"
            for route in ("/docs", "/redoc", "/openapi.json", "/.env", "/api/reports"):
                assert client.get(route).status_code == 404
            query = {"user_id": "probe", "query": "not sent to a model", "top_k": 0}
            assert client.post("/search", json=query).status_code == 401
            assert client.post("/search", json=query, headers={"X-Api-Key": service.api_key}).json() == {"data": []}
            assert client.post("/add", json={"user_id": "probe", "session_id": "s", "request_id": "r",
                "messages": [{"role": "user", "content": ""}]}, headers={"X-Api-Key": service.api_key}).status_code == 422
        report["checks"].append(phase)
        phase = "host-loopback-port-mapping"
        mapped = DockerService(report["image"]["id"], internal=False)
        services.append(mapped)
        report["host_port_probe_resources"] = {"container": mapped.name, "volume": mapped.volume, "network": mapped.network}
        mapped.prepare()
        mapped.start()  # Models disabled, no real keys, only a loopback host port.
        with mapped.client() as client:
            health = client.get("/health").json()
            assert health["embeddings"] == "none" and health["image_analysis"] is False
            assert client.post("/search", json={"user_id": "probe", "query": "hello", "top_k": 0},
                               headers={"X-Api-Key": mapped.api_key}).json() == {"data": []}
        report["checks"].append(phase)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", phase=getattr(exc, "phase", phase), error="controlled_docker_probe_failed",
                      diagnostic=type(exc).__name__,
                      hint="检查阶段、Docker 引擎、Linux 依赖、权限和端口；不打印原始日志或配置。")
    finally:
        if services:
            errors = [item for owned in reversed(services) for item in owned.cleanup()]
            report["cleanup_errors"] = errors
            report["owned_test_resources_removed"] = not errors
            if errors:
                report["status"] = "failed"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already-built local image; never pulls or builds")
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/deployment"))
    args = parser.parse_args()
    report = run_probe(args.image)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-docker-" + uuid.uuid4().hex[:8] + ".json"
    target = args.output_dir / name
    atomic_json(target, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Report:", target)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
