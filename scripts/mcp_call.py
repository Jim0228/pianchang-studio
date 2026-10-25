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
    if "text/event-stream" in response.headers.get("content-type", ""):
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
        return {"raw": response.text[:2000]}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mcp_call.py <tool> [args.json]")
        return 2
    tool = sys.argv[1]
    args = {}
    if len(sys.argv) >= 3:
        args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "ecom-video-studio", "version": "0.1.0"},
                },
            },
        )
        if response.status_code >= 400:
            print("initialize failed", response.status_code)
            return 1
        session_id = response.headers.get("mcp-session-id")
        session_headers = dict(headers)
        if session_id:
            session_headers["Mcp-Session-Id"] = session_id
        client.post(url, headers=session_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        result = client.post(
            url,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
        )
        data = parse(result)
        out = ROOT / "data" / f"mcp_{tool}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("status:", result.status_code)
        print("saved:", out)
        summary = json.dumps(data, ensure_ascii=True)
        print(summary[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
