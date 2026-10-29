#!/usr/bin/env python3
"""远程抢救: 连上卡死的单词卡后删除实验残留 .tmp 文件并重启设备。

前提: ble-agent 已连接设备(日志出现 NOTIFY_STATE AE02 err=ok)。
用法: python3 recover.py [--no-reboot]
"""
import importlib.util
import json, os, re, sys, time
from pathlib import Path

LOG = Path('/tmp/wordscan-v7.log')
WRITE = Path('/tmp/wordscan.write')
REBOOT = '--no-reboot' not in sys.argv

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('jl_auth', ROOT / 'research' / 'web-bluetooth-e87' / 'protocol-understanding' / 'jl_auth_v3.py')
jl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jl)

def out(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def crc16(data):
    c = 0
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xffff if c & 0x8000 else (c << 1) & 0xffff
    return c

class Ch:
    def __init__(self):
        LOG.touch()
        self.pos = LOG.stat().st_size
        self.seq = 1
    def send(self, data, tag=''):
        for i in range(0, len(data), 244):
            part = data[i:i+244]
            # 追加一个变化的注释位规避代理的相同写去重: hex 尾部交替加空格
            s = part.hex(' ')
            WRITE.write_text(s + ('  ' if tag == '' else ' ') + '\n')
            time.sleep(0.065)
        self._last_tag = tag
    def cmd(self, op, param, flags=0xC0, tag=''):
        frame = b'\xfe\xdc\xba' + bytes([flags, op]) + len(param).to_bytes(2, 'big') + param + b'\xef'
        self.send(frame, tag=tag or f'{op:02x}-{self.seq}')
        self.seq = (self.seq + 1) & 0xff
    def lines(self):
        size = LOG.stat().st_size
        if size < self.pos: self.pos = 0
        if size == self.pos: return []
        with LOG.open('rb') as f:
            f.seek(self.pos); data = f.read(); self.pos = f.tell()
        return data.decode('utf-8', 'replace').splitlines()
    def values(self):
        for line in self.lines():
            m = re.search(r'VALUE AE02 err=nil hex=0x([0-9a-f]+)', line)
            if m: yield bytes.fromhex(m.group(1))
    def wait(self, pred, timeout=20, desc='event'):
        end = time.time() + timeout
        while time.time() < end:
            for v in self.values():
                if 'DISCONNECTED' in str(v): break
                if pred(v): return v
            time.sleep(0.05)
        raise TimeoutError(f'timeout waiting {desc}')

def parse_fe(v):
    if len(v) < 8 or v[:3] != b'\xfe\xdc\xba' or v[-1] != 0xEF: return None
    n = int.from_bytes(v[5:7], 'big')
    if len(v) < 8 + n: return None
    return {'flags': v[3], 'op': v[4], 'body': v[7:7+n]}

def main():
    ch = Ch()
    # 等待连接就绪; 日志为追加写, 历史里最近事件是成功订阅即视为已连接
    history = LOG.read_text(errors='replace')
    if history.rfind('NOTIFY_STATE AE02 err=ok') > max(history.rfind('DISCONNECTED'), history.rfind('CONNECT_FAIL')):
        ok = True
        out('复用现有连接(日志历史验证)')
    else:
        ok = False
    end = time.time() + 120
    while not ok and time.time() < end:
        for line in ch.lines():
            if 'NOTIFY_STATE AE02 err=ok' in line: ok = True; break
        if ok: break
        time.sleep(0.2)
    if not ok:
        out('设备未连接(无 NOTIFY_STATE), 放弃'); sys.exit(1)
    out('设备已连接, 开始认证')
    ch.send(bytes([0]) + os.urandom(16), tag='auth1')
    ch.wait(lambda v: len(v) == 17 and v[0] == 1, desc='auth resp')
    ch.send(b'\x02pass', tag='auth2')
    challenge = ch.wait(lambda v: len(v) == 17 and v[0] == 0, desc='challenge')
    ch.send(bytes(jl.get_encrypted_auth_data(list(challenge))), tag='auth3')
    ch.wait(lambda v: v == b'\x02pass', desc='auth pass')
    out('认证成功')

    names = ['meter001.tmp'] + [f'token{i:03d}.tmp' for i in range(1, 16)]
    deleted = 0
    for name in names:
        seq = ch.seq; ch.seq = (ch.seq + 1) & 0xff; ch.cmd(0x23, bytes([seq, 0]) + name.encode(), tag=f"del-{name}-{seq}")
        try:
            v = ch.wait(lambda v: (parse_fe(v) or {}).get('op') == 0x23, timeout=6, desc=f'delete {name}')
            f = parse_fe(v)
            status = f['body'][0] if f['body'] else -1
            out(f'delete {name}: status={status}')
            deleted += 1
        except TimeoutError:
            out(f'delete {name}: 无响应(可能不存在或已挂)')
    out(f'删除完成, 共 {deleted}/{len(names)} 个文件有应答')

    if REBOOT:
        ch.cmd(0xE7, bytes([ch.next_seq(), 0x00]), tag='reboot')
        out('已发送重启命令(0xE7), 等待设备断连...')
        time.sleep(6)
    out('done')

if __name__ == '__main__':
    main()
