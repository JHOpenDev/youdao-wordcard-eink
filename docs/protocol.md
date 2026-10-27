# YDW011 杰理 RCSP 协议实测记录

设备：有道电子单词卡 M3，型号 YDW011，墨水屏 296×152，固件 V1.0.057_20220818，
设备信息应答含字符串 `jl_v8`（杰理协议栈）。USB 仅充电，唯一数据通道是 BLE。

## GATT

| UUID | 属性 | 用途 |
|---|---|---|
| `AE00` | 服务 | 唯一自定义服务 |
| `AE01` | Write Without Response | 手机→设备，单次 ≤244 字节（MTU 512） |
| `AE02` | Notify | 设备→手机 |

广播名 `YDW:<SN>`；广播不含服务 UUID，mfg `0xd605 02 005f 00 <MAC6 反序> 80 00...`。

## 认证（原始字节，不套 FE 帧）

```
手机 → 设备   00 <16 字节随机>            # 挑战（必须每次随机！代理会去重相同写）
设备 → 手机   01 <16 字节密文>            # 设备加密我们的随机数
手机 → 设备   02 70 61 73 73              # "pass"
设备 → 手机   00 <16 字节挑战>            # 设备出题
手机 → 设备   01 <getEncryptedAuthData(设备挑战)>   # 见 jl_auth_v3.py
设备 → 手机   02 70 61 73 73              # 认证成功
```

加密：杰理私有分组密码，SBOX/ISBOX/KS_TABLE 取自 `libjl_auth.so` 偏移
0x1b4c / 0x1c4c / 0x1d4c；static key `06775F8791 8DD42300 5DF1D8CF 0C142B`，
magic `11 22 33 33 22 11`。实现见 assets/protocol-understanding/jl_auth_v3.py
（它从同名 .so 的固定偏移读表；assets 里已附带重构的表文件）。
session-init 帧 `FEDCBAC00600020001EF`（0x06）实测**不是必须**。

## FE 帧格式

```
FE DC BA | flags | cmd | len(BE16) | body... | EF
```
flags bit7=1 请求（0xC0 带响应 / 0x80 不带），bit7=0 应答。应答 body 首字节为 status，0x00 成功。
`opCodeSn`（body 首字节）是跨命令单调计数，控制帧与数据帧共用。

| cmd | 名称 | 说明 |
|---|---|---|
| 0x03 | TargetInfo | 应答含固件串 `jl_v8`、MAC、版本 |
| 0x07 | SysInfo | 运行配置（电量、模式等） |
| 0x21 | PrepareEnv | body `[seq,0x00]`；开传输前必须 |
| 0x1B | StartLargeFileTransfer | 文件声明，见下 |
| 0x1D | 窗口 ACK（0x80 方向） | body `[ackSeq][status][buffer BE16][offset BE32]` |
| 0x01 | CMD_DATA（0x80 方向） | xmOpCode=0x1D 的数据帧，见下 |
| 0x1C | Stop | 设备可主动发：body `[seq][reason]`，reason=02 校验失败 |
| 0x1E | Cancel | 取消当前传输 |
| 0x23 | DeleteFileByName | body `[seq][0x00][name]`；实测不清除传输指针 |
| 0xE7 | Reboot | body `[seq][0x00]`；设备重启断连，不清 Flash 断点 |

## 文件传输（设备拉取模式）

1. `0x21` 准备。
2. `0x1B` body：`[seq][size 4B BE][wholeCRC 2B][filename 12字符+NUL]`。
   **文件名必须恰好 12 字符**（如 `meter001.tmp`），长度不对则设备静默不响应。
   wholeCRC 为整文件 CRC16/XMODEM（poly 0x1021, init 0）。
   应答 `00 1B 00 04 00 seq 02 00 EF` 中的 `0x0200` 是文件 CRC 标志位。
3. 设备发首个窗口 ACK：`80 1D` body `[ackSeq][status][buffer BE16][offset BE32]`
   （典型 buffer=0x1000=4096，offset 可能非 0——Flash 里有同名文件断点，跨重启保留）。
4. 从 offset 取 buffer 字节，按 **512 字节**分块，每块一帧：
   `FE DC BA 80 01 [len] [seq][1D][chunkIdx][chunkCRC BE16][≤512B 数据] EF`
   chunkIdx 每窗口 0..7。帧超过 244 字节时按 244 分片写入 AE01，片间 ≥65ms。
5. 每发完一窗口等下一个 ACK，按新 offset 继续。
6. **最终 ACK 的 offset=0（且 buffer=0）即传输完成——立即停止，不要再发数据。**
   多发的数据会让设备发出 `0x1C` reason=02（校验失败）。此时也无需发 0x1C 应答流程。

## 图片格式

直接推 JPEG（296×152，PIL quality 60–75 实测均可），无需自定义文件头。
断点续传恰好命中 JPEG 头部（前 512 字节逐次相同），同名续传因此能通过整文件 CRC。
若某次头部字节变化，换新文件名或先 0x1E 取消即可。

## 实现参考

- assets/agent2.swift —— CoreBluetooth 代理（扫描/连接/收发/日志）
- assets/bridge.py —— 认证 + 传输 + 渲染推送（`upload()` 即完整状态机）
- assets/meter.py —— token 统计
- 上游研究：github hybridherbst/web-bluetooth-e87（jl-auth.ts 加密表、E87 抓包）、
  jumpingmushroom/e87_badge（真实 ae01 抓包与 jadx 笔记）、
  MinatiScape/boat-crest 与 auroraphtgrp01/smart-health（jl_rcsp 反编译源码）
