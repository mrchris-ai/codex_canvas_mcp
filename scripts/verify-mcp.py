#!/usr/bin/env python3
"""Read-only stdio check of a rebuilt Canvas MCP launcher.

Makes exactly two tool calls: canvas_get_write_policy (local, no Canvas request)
and one canvas_read_api GET of /api/v1/courses/<id>. Prints only summary fields.
Usage: verify-mcp.py LAUNCHER COURSE_ID [EXPECT_VERSION] [EXPECT_TOOLS]
"""
import json, subprocess, sys

launcher = sys.argv[1]
course = sys.argv[2]
want_ver = sys.argv[3] if len(sys.argv) > 3 else "0.21.2"
want_tools = int(sys.argv[4]) if len(sys.argv) > 4 else 48

p = subprocess.Popen([launcher], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True)
n = 0
def rpc(method, params=None):
    global n; n += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    msg = json.loads(p.stdout.readline())
    if "error" in msg:
        raise SystemExit(f"FAIL {method}: {msg['error'].get('message')}")
    return msg["result"]
def call(name, args):
    return json.loads(rpc("tools/call", {"name": name, "arguments": args})["content"][0]["text"])

fails = 0
info = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "verify-mcp", "version": "1"}})["serverInfo"]
tools = rpc("tools/list")["tools"]
print(f"server={info['name']} version={info['version']} tools={len(tools)}")
fails += info["version"] != want_ver or len(tools) != want_tools

pol = call("canvas_get_write_policy", {})
lists = {k: v for k, v in pol.items() if isinstance(v, list)}
nonempty = [k for k, v in lists.items() if v]
print(f"write_policy enabled={pol.get('enabled')} connector_version={pol.get('connector_version')} "
      f"list_fields={len(lists)} nonempty={nonempty or 'none'}")
fails += pol.get("enabled") is not False or bool(nonempty)

c = call("canvas_read_api", {"path": f"/api/v1/courses/{course}"})
body = c.get("data", c) if isinstance(c, dict) else c
if isinstance(body, dict):
    print("course " + " ".join(f"{k}={body.get(k)}" for k in ("id", "name", "course_code", "workflow_state")))
else:
    print(f"course response type={type(body).__name__}")
p.stdin.close(); p.wait(timeout=10)
sys.exit(1 if fails else 0)
