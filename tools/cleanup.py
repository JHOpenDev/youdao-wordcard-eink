#!/usr/bin/env python3
"""批量删除设备上我们创建的文件并重启。用法: python3 cleanup.py"""
import importlib.util, os, re, sys, time
from pathlib import Path
LOG = Path('/tmp/wordscan-v7.log'); WRITE = Path('/tmp/wordscan.write')
ROOT = Path(__file__).resolve().parent
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
        frame = b'\xfe\xdc\xba\xc0' + bytes([op]) + len(param).to_bytes(2,'big') + param + b'\xef'
        self.send(frame, tag=tag)
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
names = [l.strip() for l in open('/tmp/device-files.txt') if l.strip()]
out(f'待删除文件: {len(names)} 个')
ch = Ch()
# 认证
ch.send(bytes([0]) + os.urandom(16), tag='a1')
if ch.wait(lambda v: len(v)==17 and v[0]==1, 15) is None: out('认证无响应'); sys.exit(1)
ch.send(b'\x02pass', tag='a2')
c = ch.wait(lambda v: len(v)==17 and v[0]==0, 15)
if c is None: out('挑战无响应'); sys.exit(1)
ch.send(bytes(jl.get_encrypted_auth_data(list(c))), tag='a3')
if ch.wait(lambda v: v == b'\x02pass', 15) is None: out('认证失败'); sys.exit(1)
out('认证成功, 开始删除')
ok = 0
for i, name in enumerate(names):
    ch.cmd(0x23, bytes([ch.next_seq(), 0]) + name.encode(), tag=f'd{i}')
    v = ch.wait(lambda v: (parse_fe(v) or {}).get('op') == 0x23, 4)
    st = (parse_fe(v) or {}).get('body', [99])[0] if v else None
    if st == 0: ok += 1
    if i % 25 == 0: out(f'进度 {i+1}/{len(names)} (成功 {ok})')
out(f'删除完成: {ok}/{len(names)}')
time.sleep(1)
ch.cmd(0xE7, bytes([ch.next_seq(), 0x00]), tag='rb')
out('已发送重启')
