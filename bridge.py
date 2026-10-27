#!/usr/bin/env python3
"""YDW011 (有道单词卡 M3) 墨水屏实时推送桥接。

依赖 ble-agent/agent.swift 编译的 CoreBluetooth 代理完成实际 BLE 收发：
  - 代理日志:   /tmp/wordscan.log        (本脚本解析设备通知)
  - 写入通道:   /tmp/wordscan.write      (每行一段 hex, 代理按 244B 分片写入 AE01)
  - 连接目标:   /tmp/wordscan.target     (macOS peripheral UUID)

本进程负责 Jieli RCSP 认证、文件传输状态机、token 图片渲染与定时刷新。
"""
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
LOG = Path(os.environ.get('WORDCARD_AGENT_LOG', '/tmp/wordscan.log'))
WRITE = Path('/tmp/wordscan.write')
STATS = Path(os.environ.get('WORDCARD_STATS', '/tmp/tokenmeter.json'))
TARGET_UUID = os.environ.get('WORDCARD_TARGET_UUID', '')
INTERVAL = float(os.environ.get('TOKEN_DISPLAY_INTERVAL', '20'))
FONT_PATH = os.environ.get('WORDCARD_FONT', '/System/Library/Fonts/Supplemental/Arial Bold.ttf')

# 认证加密模块(源自 web-bluetooth-e87 的逆向表, MIT)
spec = importlib.util.spec_from_file_location('jl_auth', ROOT / 'auth' / 'jl_auth_v3.py')
jl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jl)


def out(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    return crc


class Channel:
    """通过代理的文件接口收发 BLE 数据, 并增量消费代理日志。"""

    def __init__(self):
        LOG.touch()
        history = LOG.read_text(errors='replace')
        # 代理日志为追加写; 以"最后一条关键事件"判断当前是否处于已连接状态,
        # 使桥接重启时能立刻复用已建立的连接。
        last_notify = history.rfind('NOTIFY_STATE AE02 err=ok')
        last_bad = max(history.rfind('DISCONNECTED'), history.rfind('CONNECT_FAIL'))
        self.connected_hint = last_notify > last_bad
        self.pos = LOG.stat().st_size
        self.seq = 1

    def send(self, data):
        for i in range(0, len(data), 244):  # CoreBluetooth 单次 WNR 244B
            WRITE.write_text(data[i:i + 244].hex(' ') + '\n')
            time.sleep(0.065)

    def cmd(self, op, param, flags=0xC0):
        frame = b'\xfe\xdc\xba' + bytes([flags, op]) + len(param).to_bytes(2, 'big') + param + b'\xef'
        self.send(frame)
        self.seq = (self.seq + 1) & 0xFF

    def next_seq(self):
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return s

    def lines(self):
        size = LOG.stat().st_size
        if size < self.pos:
            self.pos = 0
        if size == self.pos:
            return []
        with LOG.open('rb') as f:
            f.seek(self.pos)
            data = f.read()
            self.pos = f.tell()
        return data.decode('utf-8', 'replace').splitlines()

    def wait(self, pred, timeout=30, desc='event'):
        end = time.time() + timeout
        while time.time() < end:
            for line in self.lines():
                if 'DISCONNECTED' in line or 'CONNECT_FAIL' in line:
                    raise RuntimeError('BLE disconnected')
                v = parse_value(line)
                if v is not None and pred(v):
                    return v
            time.sleep(0.05)
        raise TimeoutError(f'timeout waiting {desc}')


def parse_value(line):
    m = re.search(r'VALUE AE02 err=nil hex=0x([0-9a-f]+)', line)
    return bytes.fromhex(m.group(1)) if m else None


def parse_fe(v):
    if len(v) < 8 or v[:3] != b'\xfe\xdc\xba' or v[-1] != 0xEF:
        return None
    n = int.from_bytes(v[5:7], 'big')
    if len(v) < 8 + n:
        return None
    return {'flags': v[3], 'op': v[4], 'body': v[7:7 + n]}


def parse_ack(v):
    f = parse_fe(v)
    if not f or f['op'] != 0x1D or len(f['body']) < 8:
        return None
    b = f['body']
    return {'seq': b[0], 'status': b[1],
            'buffer': int.from_bytes(b[2:4], 'big'),
            'offset': int.from_bytes(b[4:8], 'big')}


def authenticate(ch):
    out('BLE connected; authenticating')
    ch.send(bytes([0]) + os.urandom(16))                       # 挑战(必须随机, 代理会去重相同写)
    ch.wait(lambda v: len(v) == 17 and v[0] == 1, desc='auth response')
    ch.send(b'\x02pass')
    challenge = ch.wait(lambda v: len(v) == 17 and v[0] == 0, desc='device challenge')
    ch.send(bytes(jl.get_encrypted_auth_data(list(challenge))))
    ch.wait(lambda v: v == b'\x02pass', desc='auth success')
    out('authentication OK')
    time.sleep(0.2)


def render(path):
    s = json.loads(STATS.read_text())
    im = Image.new('RGB', (296, 152), 'white')
    d = ImageDraw.Draw(im)
    big = ImageFont.truetype(FONT_PATH, 27)
    small = ImageFont.truetype(FONT_PATH, 15)
    d.text((8, 5), 'ZCode TOKENS LIVE', font=small, fill='black')
    d.text((8, 29), f"{s['total']:,}", font=big, fill='black')
    d.text((8, 66), f"IN {s['in']:,}", font=small, fill='black')
    d.text((8, 86), f"OUT {s['out']:,}", font=small, fill='black')
    d.text((8, 110), f"REQ {s['req']}  RATE {s.get('rate5', 0)}/min", font=small, fill='black')
    im.save(path, 'JPEG', quality=75, optimize=True)
    return s


def upload(ch, image, name=b'meter001.tmp'):
    """设备拉取模式: 按 0x1D ACK 指定的偏移发数; 最终 ACK(offset=0)即完成, 勿再多发。"""
    ch.cmd(0x21, bytes([ch.next_seq(), 0]))
    ch.wait(lambda v: (parse_fe(v) or {}).get('op') == 0x21, desc='prepare env')
    data = Path(image).read_bytes()
    assert len(name) == 13, '固件要求文件名恰好 12 字符 + NUL'
    param = bytes([ch.next_seq()]) + len(data).to_bytes(4, 'big') + crc16(data).to_bytes(2, 'big') + name
    ch.cmd(0x1B, param)
    ack = parse_ack(ch.wait(lambda v: parse_ack(v), desc='initial transfer window'))
    windows = 0
    seen = set()
    while True:
        off, buf = ack['offset'], ack['buffer']
        if off == 0 and windows > 0:
            out(f'UPLOAD_OK bytes={len(data)} windows={windows}')
            return True
        if off in seen:
            ack = parse_ack(ch.wait(lambda v: parse_ack(v), desc='next transfer window'))
            continue
        seen.add(off)
        if off > len(data):
            raise RuntimeError(f'bad device offset {off}>{len(data)}')
        window = data[off:off + buf]
        if not window:
            out(f'UPLOAD_OK bytes={len(data)} windows={windows} (empty final window)')
            return True
        for idx in range((len(window) + 511) // 512):
            chunk = window[idx * 512:(idx + 1) * 512]
            param = bytes([ch.next_seq(), 0x1D, idx]) + crc16(chunk).to_bytes(2, 'big') + chunk
            ch.cmd(0x01, param, flags=0x80)
        windows += 1
        out(f'window {windows}: offset={off} bytes={len(window)}')
        ack = parse_ack(ch.wait(lambda v: parse_ack(v), timeout=40, desc='window ACK'))


def wait_connection(ch):
    if ch.connected_hint:
        return True
    end = time.time() + 180
    while time.time() < end:
        for line in ch.lines():
            if 'NOTIFY_STATE AE02 err=ok' in line:
                return True
        time.sleep(0.2)
    return False


def main():
    if not TARGET_UUID:
        sys.exit('请设置 WORDCARD_TARGET_UUID (从代理日志的 DISCOVER 行复制目标 peripheral UUID)')
    Path('/tmp/wordscan.target').write_text(TARGET_UUID + '\n')
    ch = Channel()
    out('token display bridge started')
    while True:
        try:
            if not wait_connection(ch):
                out('waiting for device broadcast (按一下单词卡任意键可唤醒)'); time.sleep(5); continue
            authenticate(ch)
            last_image = None
            while True:
                image = '/tmp/ydw-token.jpg'
                stats = render(image)
                payload = Path(image).read_bytes()
                if payload == last_image:
                    time.sleep(INTERVAL); continue
                upload(ch, image)
                last_image = payload
                out(f"display updated: total={stats['total']:,} req={stats['req']}")
                time.sleep(INTERVAL)
        except Exception as e:
            out(f'bridge retry: {type(e).__name__}: {e}')
            traceback.print_exc()
            time.sleep(8)
            ch = Channel()


if __name__ == '__main__':
    main()
