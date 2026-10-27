#!/usr/bin/env python3
"""token-meter: 实时统计 ZCode 会话 token 消耗。

数据源: ~/.zcode/cli/rollout/model-io-session_*.jsonl (每行一个模型请求, response.usage)
输出:
  /tmp/tokenmeter.json  机器可读统计
  /tmp/tokenmeter.txt   墨水屏友好的纯文本 (<=6 行)
  http://127.0.0.1:8787/stats  JSON
  http://127.0.0.1:8787/       网页面板
"""
import json, os, glob, time, threading, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLLDIR = os.path.expanduser("~/.zcode/cli/rollout")
POLL = 2.0

lock = threading.Lock()
state = {
    "sessionFile": None, "sessionId": None,
    "req": 0, "in": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0,
    "byModel": {}, "recent": [],  # (epoch, totalTokens)
    "firstAt": None, "lastAt": None, "updatedAt": None,
}

def parse_usage(r):
    u = (r.get("response") or {}).get("usage") or {}
    it = u.get("inputTokens")
    if it is None:
        it = u.get("input_tokens", 0)
        ot = u.get("output_tokens", 0)
        cr = u.get("cache_read_input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
    else:
        ot = u.get("outputTokens", 0)
        cr = u.get("cacheReadTokens", 0) or 0
        cw = u.get("cacheWriteTokens", 0) or 0
    return int(it or 0), int(ot or 0), int(cr or 0), int(cw or 0)

def absorb(path, offset):
    """从 offset 读取新行并累计; 返回新 offset"""
    size = os.path.getsize(path)
    if size < offset:  # 文件被截断/轮转, 重来
        offset = 0
        with lock:
            state.update({"req": 0, "in": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0,
                          "total": 0, "byModel": {}, "recent": [], "firstAt": None, "lastAt": None})
    if size == offset:
        return offset
    added = 0
    with open(path, "rb") as f:
        f.seek(offset)
        # 保证从完整行边界开始
        if offset == 0:
            pass
        chunk = f.read(size - offset)
        # 若最后一行不完整, 回退到最后一个换行
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return offset
        chunk = chunk[:cut + 1]
        added = len(chunk)
    for line in chunk.splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        u = (r.get("response") or {}).get("usage")
        if not u:
            continue
        it, ot, cr, cw = parse_usage(r)
        tot = it + ot
        model = ((r.get("model") or {}).get("modelId")) or "?"
        ts = None
        for k in ("completedAt", "startedAt"):
            v = r.get(k)
            if v:
                try:
                    ts = time.mktime(time.strptime(v[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    ts = None
            if ts: break
        ts = ts or time.time()
        with lock:
            state["req"] += 1
            state["in"] += it; state["out"] += ot
            state["cacheRead"] += cr; state["cacheWrite"] += cw
            state["total"] += tot
            state["byModel"][model] = state["byModel"].get(model, 0) + tot
            state["recent"].append((ts, tot))
            state["firstAt"] = state["firstAt"] or ts
            state["lastAt"] = ts
            if r.get("sessionId"): state["sessionId"] = r["sessionId"]
    return offset + added

def track_offsets():
    off = {}
    while True:
        files = glob.glob(os.path.join(ROLLDIR, "model-io-session_*.jsonl"))
        if not files:
            time.sleep(POLL); continue
        cur = max(files, key=os.path.getmtime)
        with lock:
            if state["sessionFile"] != cur:
                state["sessionFile"] = cur
        off[cur] = absorb(cur, off.get(cur, 0))
        # 清理旧文件 offset
        for k in list(off):
            if k != cur: off.pop(k, None)
        # 速率与输出
        now = time.time()
        with lock:
            state["recent"] = [(t, v) for (t, v) in state["recent"] if now - t <= 3600]
            state["updatedAt"] = now
            rate5 = sum(v for (t, v) in state["recent"] if now - t <= 300) / 5.0
            snap = dict(state)
        write_outputs(snap, rate5)
        time.sleep(POLL)

def fmt(n):
    return f"{n:,}"

def render_txt(s, rate5):
    el = ""
    if s["firstAt"]:
        mins = max(1, int((s["lastAt"] - s["firstAt"]) / 60))
        el = f"{mins}m"
    lines = [
        "ZCode TOKENS",
        f"TOTAL {fmt(s['total'])}",
        f"IN {fmt(s['in'])}  OUT {fmt(s['out'])}",
        f"CACHE {fmt(s['cacheRead'])}",
        f"REQ {s['req']}  {fmt(int(rate5))}/min  {el}",
    ]
    return "\n".join(lines) + "\n"

DASH = """<!doctype html><html><head><meta charset="utf-8"><title>Token Meter</title>
<style>body{background:#111;color:#eee;font:600 22px ui-monospace,Menlo;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#1b1b1b;border:1px solid #333;border-radius:16px;padding:32px 40px;min-width:420px}
h1{font-size:15px;color:#9ad;margin:0 0 14px;letter-spacing:2px}
.r{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #333}
.v{color:#7f7}.small{font-size:13px;color:#888;margin-top:12px}</style></head><body>
<div class="card"><h1>ZCODE TOKEN METER</h1><div id="x">loading…</div><div class="small" id="t"></div></div>
<script>async function up(){try{const r=await fetch('/stats');const s=await r.json();
document.getElementById('x').innerHTML=
`<div class="r"><span>TOTAL</span><b class="v">${s.total.toLocaleString()}</b></div>
<div class="r"><span>IN</span><span>${s.in.toLocaleString()}</span></div>
<div class="r"><span>OUT</span><span>${s.out.toLocaleString()}</span></div>
<div class="r"><span>CACHE READ</span><span>${s.cacheRead.toLocaleString()}</span></div>
<div class="r"><span>REQUESTS</span><span>${s.req}</span></div>
<div class="r"><span>RATE(5min)</span><span>${Math.round(s.rate5).toLocaleString()}/min</span></div>
<div class="r"><span>MODEL</span><span>${Object.keys(s.byModel).join(', ')}</span></div>`;
document.getElementById('t').textContent='session: '+s.sessionId+' · updated '+new Date(s.updatedAt*1000).toLocaleTimeString();}catch(e){}}
up();setInterval(up,2000);</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/stats":
            with lock:
                s = dict(state); s["byModel"] = dict(state["byModel"]); s["recent"] = []
            now = time.time()
            with lock:
                rate5 = sum(v for (t, v) in state["recent"] if now - t <= 300) / 5.0
            s["rate5"] = round(rate5)
            body = json.dumps(s, ensure_ascii=False).encode()
            self._send(200, body, "application/json")
        elif self.path == "/":
            self._send(200, DASH.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"no", "text/plain")
    def log_message(self, *a): pass

def write_outputs(s, rate5):
    with open("/tmp/tokenmeter.json", "w") as f:
        json.dump({k: v for k, v in s.items() if k != "recent"} | {"rate5": round(rate5)}, f, ensure_ascii=False)
    with open("/tmp/tokenmeter.txt", "w") as f:
        f.write(render_txt(s, rate5))

if __name__ == "__main__":
    threading.Thread(target=track_offsets, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 8787), H)
    print("meter: http://127.0.0.1:8787/  data=/tmp/tokenmeter.txt")
    srv.serve_forever()
