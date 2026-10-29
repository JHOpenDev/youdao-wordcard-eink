#!/usr/bin/env python3
"""单次验证传输: 新状态机真机首秀, 全程日志。"""
import sys, time, traceback
sys.path.insert(0, '/Users/JH/wordcard')
import bridge as B
from pathlib import Path

LOG = Path(B.LOG)
out = B.out

mark = LOG.stat().st_size
out('等待全新连接(设备重新广播约 40-90 秒)...')
end = time.time() + 300
ready = False
while time.time() < end and not ready:
    size = LOG.stat().st_size
    if size > mark:
        with LOG.open('rb') as f:
            f.seek(mark); data = f.read(); mark = f.tell()
        if 'NOTIFY_STATE AE02 err=ok' in data.decode('utf-8', 'replace'):
            ready = True
    time.sleep(1)
if not ready:
    out('超时: 未建立新连接'); sys.exit(1)
out('全新连接就绪, 开始验证传输(认证后立即传输)')

ch = B.Channel()
try:
    import os
    if '--auth' in sys.argv:
        B.authenticate(ch)
    else:
        out('跳过认证(连接已认证)')  # 同连接二次认证会被设备忽略
    image = '/tmp/ydw-token.jpg'
    stats = B.render(image)
    t0 = time.time()
    ok = B.upload(ch, image)
    out(f'RESULT: {"OK" if ok else "FAILED"} in {time.time()-t0:.1f}s')
    if ok:
        out('display pushed (llm-usage panel)')
except Exception as e:
    out(f'EXCEPTION: {type(e).__name__}: {e}')
    traceback.print_exc()
    try:
        ch.cmd(0x1E, bytes([ch.next_seq()]))
        out('sent CANCEL(0x1E) after exception')
    except Exception:
        pass
