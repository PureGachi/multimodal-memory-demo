"""Shared controlled HTTP assertions; never reads settings or invokes model clients."""
import base64
import io
from PIL import Image


class ProbeFailure(RuntimeError):
    def __init__(self, phase):
        super().__init__("Controlled HTTP probe failed")
        self.phase = phase


def exercise_http(client, api_key, restart, checks):
    phase = "prepare-http-fixtures"
    try:
        def check(ok):
            if not ok:
                raise RuntimeError("Deployment probe check failed")
        headers = {"X-Api-Key":api_key}
        user, request = "deployment-probe-user", "deployment-probe-request"
        content = [{"type":"text","text":"unique-image-evidence capacity-token before"}]
        for fmt, mime in (("PNG","png"),("JPEG","jpeg"),("WEBP","webp")):
            stream = io.BytesIO()
            Image.new("RGB", (8,8), "red").save(stream, fmt)
            content.append({"type":"image_url","image_url":{"url":f"data:image/{mime};base64," + base64.b64encode(stream.getvalue()).decode()}})
        content.append({"type":"text","text":"after"})
        payload = {"request_id":request,"user_id":user,"session_id":"s","messages":[
            {"role":"assistant","timestamp":1704067200000,"content":content},
            *[{"role":"user","content":f"capacity-token item {i}"} for i in range(110)]]}
        phase = "authentication"
        check(client.post("/add", json=payload).status_code == 401)
        check(client.get("/health").status_code == 200)
        checks.append(phase)
        phase = "synchronous-add-idempotency"
        response = client.post("/add", json=payload, headers=headers)
        check(response.status_code == 200)
        check(response.json() == {"success":True,"request_id":request,"user_id":user,"session_id":"s"})
        check(client.post("/add",json=payload,headers=headers).json() == response.json())
        checks.append(phase)
        phase = "top-k-100-and-zero"
        query = {"query":"capacity-token","user_id":user,"top_k":100}
        data = client.post("/search", json=query, headers=headers).json()["data"]
        check(len(data)==100 and len({item["id"] for item in data})==100)
        ids = [item["id"] for item in data]
        check(client.post("/search",json={**query,"top_k":0},headers=headers).json()=={"data":[]})
        check(client.post("/search",json={**query,"top_k":101},headers=headers).status_code==422)
        checks.append(phase)
        phase = "ordered-images-and-query-image"
        image_query = {"query":[content[1]],"user_id":user,"top_k":100}
        restored = client.post("/search",json=image_query,headers=headers).json()["data"]
        check(len(restored)==1 and restored[0]["content"]==content)
        image_id = restored[0]["id"]
        checks.append(phase)
        phase = "bearer-token-and-isolation"
        for authorization in ("Bearer ", "Token "):
            check(client.post("/search",json=image_query,headers={"Authorization":authorization+api_key}).status_code==200)
        check(client.post("/search",json={**query,"user_id":"other-user"},headers=headers).json()=={"data":[]})
        checks.append(phase)
        phase = "conflict-body-limit-private-paths"
        conflict={**payload,"session_id":"changed"}
        check(client.post("/add",json=conflict,headers=headers).status_code==409)
        check(client.post("/add",content=b"x"*1048577,headers=headers).status_code==413)
        for path in ("/api/reports","/data","/.env"):
            check(client.get(path).status_code==404)
        checks.append(phase)
        phase = "restart-durability"
        restart()
        data = client.post("/search",json=query,headers=headers).json()["data"]
        check([item["id"] for item in data]==ids)
        restored = client.post("/search",json=image_query,headers=headers).json()["data"]
        check(restored[0]["id"]==image_id and restored[0]["content"]==content)
        checks.append(phase)
    except Exception:
        raise ProbeFailure(phase) from None
