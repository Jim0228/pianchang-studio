from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
cfg = dotenv_values(ROOT / ".env")
url = cfg.get("MCP_VIDEO_URL", "")
token = cfg.get("MCP_VIDEO_TOKEN", "")
if not url or not token:
    raise SystemExit("MCP_VIDEO_URL / MCP_VIDEO_TOKEN missing in .env")

headers = {
    "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def parse(response: httpx.Response):
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        last = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    last = json.loads(payload)
                except json.JSONDecodeError:
                    continue
        return last
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:1000]}


with httpx.Client(timeout=90.0, follow_redirects=True) as client:
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ecom-video-studio", "version": "0.1.0"},
        },
    }
    response = client.post(url, headers=headers, json=init_payload)
    print("initialize:", response.status_code, response.headers.get("content-type"))
    print(json.dumps(parse(response), ensure_ascii=False)[:2000])
    session_id = response.headers.get("mcp-session-id")
    print("session:", bool(session_id))

    if response.status_code >= 400:
        sys.exit(1)

    session_headers = dict(headers)
    if session_id:
        session_headers["Mcp-Session-Id"] = session_id

    client.post(
        url,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )

    tools_response = client.post(
        url,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    print("tools:", tools_response.status_code, tools_response.headers.get("content-type"))
    data = parse(tools_response)
    out = ROOT / "data" / "mcp_tools.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tools = (data or {}).get("result", {}).get("tools", [])
    for tool in tools:
        name = tool.get("name", "")
        required = (tool.get("inputSchema") or {}).get("required") or []
        print(f"{name} | required={required}")
    print("saved:", out)
