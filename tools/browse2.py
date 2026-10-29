#!/usr/bin/env python3
"""按簇号链表浏览设备 FS: 0x12 + PathData(type,readNum,start,devHandler,path)."""
import importlib.util, os, re, sys, time
from pathlib import Path
LOG = Path('/tmp/wordscan-v7.log'); WRITE = Path('/tmp/wordscan.write')
ROOT = Path('/Users/jh/wordcard')
spec = importlib.util.spec_from_file_location('jl_auth', ROOT/'research/web-bluetooth-e87/protocol-understanding/jl_auth_v3.py')
jl = importlib.util.module_from_spec(spec); spec.loader.exec_module(jl)
def out(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
class Ch:
    def __init__(self):
        self.pos = LOG.stat().st_size; self.seq = 1
    def send(self, data, tag=''):
        for i in range(0, len(data), 244):
            WRITE.write_text(data[i:i+244].hex(' ') + ('\n' if tag == '' else ' \n'))
            time.sleep(0.25)
    def cmd(self, op, param, tag=''):
        self.send(b'\xfe\xdc\xba\xc0' + bytes([op]) + len(param).to_bytes(2,'big') + param + b'\xef', tag=tag)
        self.seq = (self.seq + 1) & 0xff
    def next_seq(self):
        s = self.seq; self.seq = (self.seq + 1) & 0xff; return s
    def values(self):
        size = LOG.stat().st_size
        if size < self.pos: self.pos = 0
        if size == self.pos: return
        with LOG.open('rb') as f:
            f.seek(self.pos); data = f.read(); self.pos = f.tell()
        for line in data.decode('utf-8','replace').splitlines():
            m = re.search(r'VALUE AE02 err=nil hex=0x([0-9a-f]+)', line)
            if m: yield bytes.fromhex(m.group(1))
    def wait(self, pred, timeout=6, desc=''):
        end = time.time() + timeout
        while time.time() < end:
            for v in self.values():
                if pred(v): return v
            time.sleep(0.05)
        return None
def parse_fe(v):
    if len(v) < 8 or v[:3] != b'\xfe\xdc\xba' or v[-1] != 0xEF: return None
    n = int.from_bytes(v[5:7],'big')
    if len(v) < 8+n: return None
    return {'flags': v[3], 'op': v[4], 'body': v[7:7+n]}
def pathdata(ctype, read_num, start, dev, clusters):
    b = bytearray()
    b.append(ctype); b.append(read_num)
    b += start.to_bytes(2,'big'); b += dev.to_bytes(4,'big')
    b += (len(clusters)*4).to_bytes(2,'big')
    for c in clusters: b += c.to_bytes(4,'big')
    return bytes(b)
ch = Ch()
h = LOG.read_text(errors='replace')
if h.rfind('NOTIFY_STATE AE02 err=ok') <= max(h.rfind('DISCONNECTED'), h.rfind('CONNECT_FAIL')):
    out('设备未连接(先 kickstart 代理)'); sys.exit(1)
out('连接复用, 认证...')
ch.send(bytes([0]) + os.urandom(16), tag='a1')
if ch.wait(lambda v: len(v)==17 and v[0]==1, 12) is None: out('认证无响应'); sys.exit(1)
ch.send(b'\x02pass', tag='a2')
c = ch.wait(lambda v: len(v)==17 and v[0]==0, 12)
if c is None: out('无挑战'); sys.exit(1)
ch.send(bytes(jl.get_encrypted_auth_data(list(c))), tag='a3')
if ch.wait(lambda v: v == b'\x02pass', 12) is None: out('认证失败'); sys.exit(1)
out('认证成功')
for label, pd in [('root path=[0]', pathdata(0x00, 0x0A, 0, 0, [0])),
                  ('root path=[2]', pathdata(0x00, 0x0A, 0, 0, [2])),
                  ('root path=[1]', pathdata(0x00, 0x0A, 0, 0, [1]))]:
    out(f'browse {label}')
    ch.cmd(0x12, bytes([ch.next_seq()]) + pd, tag=label)
    end = time.time() + 5
    n = 0
    while time.time() < end:
        for v in ch.values():
            f = parse_fe(v)
            if f:
                txt = ''.join(chr(cc) if 32 <= cc < 127 else '.' for cc in f['body'])
                print(f"  op=0x{f['op']:02x} len={len(f['body']):4d} {f['body'][:16].hex()} | {txt[:100]}", flush=True)
                n += 1
            elif len(v) > 12:
                txt = ''.join(chr(cc) if 32 <= cc < 127 else '.' for cc in v)
                print(f"  raw len={len(v):4d} | {txt[:110]}", flush=True)
                n += 1
        if n: break
    if not n: print('  (无响应)')
    time.sleep(0.5)
ch.cmd(0x13, bytes([ch.next_seq(), 0xFF]), tag='stop')
out('done')
