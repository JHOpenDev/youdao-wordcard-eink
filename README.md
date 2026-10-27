# 有道单词卡 M3 墨水屏改造 · 实时数据显示器

**Turn a Youdao Word Card M3 (YDW011) e-ink device into a real-time BLE-driven display.**

把网易有道电子单词卡 M3（型号 YDW011，墨水屏 296×152）改造成 Mac 通过蓝牙驱动的
实时信息屏。本项目逆向了它的杰理(JieLi) BLE 私有协议（认证 + 图片文件传输），
并附带一个完整可跑的示例：把 ZCode 会话的 token 消耗实时推送到墨水屏。

> 效果：墨水屏每 20 秒刷新一张面板，显示当前会话累计 token、IN/OUT、请求次数与速率；
> 数字不变时自动跳过刷新。数据源、协议、传输状态机均经过真机验证。

## 架构

```
~/.zcode/cli/rollout/model-io-session_*.jsonl   (模型请求 usage 流水)
        │  meter.py   增量解析, 2s 轮询
        ▼
/tmp/tokenmeter.json  +  http://127.0.0.1:8787  (网页面板 / JSON)
        │  bridge.py  渲染 296×152 JPEG + RCSP 认证 + 文件传输
        ▼
/tmp/wordscan.write (hex 帧)  ──►  ble-agent (Swift/CoreBluetooth, launchd)
        │  BLE: 服务 AE00, 写 AE01, 通知 AE02
        ▼
有道单词卡 M3 墨水屏 (设备按 512B 块 / 4KB 窗口主动拉取, 自己完成 JPEG 解码与刷屏)
```

组成：

| 组件 | 说明 |
|---|---|
| `meter.py` | token 统计守护进程（数据源可替换成任何 JSON 统计） |
| `bridge.py` | 认证、RCSP 传输状态机、图片渲染与定时推送 |
| `ble-agent/agent.swift` | CoreBluetooth 代理：扫描/连接/收发，通过文件接口受控 |
| `auth/` | 杰理认证加密（源自开源逆向项目的表 + 实现，MIT） |
| `docs/protocol.md` | 协议逆向全记录（帧格式、认证、传输状态机、坑） |
| `docs/macos-ble-pitfalls.md` | macOS 蓝牙/LaunchAgent 三大坑实录 |
| `launchd/` | 常驻服务 plist 模板 |

## 快速开始

前提：macOS (Apple Silicon)，Xcode CLT（`swiftc`），Python 3 + Pillow，
一台开机状态的有道单词卡（M3/YDW011 或同系杰理方案机型）。

```bash
# 1. 编译 BLE 代理并安装 launchd 服务（蓝牙必须由 LaunchAgent 拉起，见 docs/macos-ble-pitfalls.md）
cd ble-agent && swiftc -O agent.swift -o WordScan.app/Contents/MacOS/wordscan   # 先建好 bundle 结构，见 launchd/ 说明
cp launchd/*.plist ~/Library/LaunchAgents/   # 改里面的路径与用户名
launchctl load ~/Library/LaunchAgents/local.wordscan.plist

# 2. 启动统计与桥接（先从 /tmp/wordscan.log 的 DISCOVER 行找到设备的 peripheral UUID）
launchctl load ~/Library/LaunchAgents/local.tokenmeter.plist
WORDCARD_TARGET_UUID=<uuid> launchctl load ~/Library/LaunchAgents/local.tokenbridge.plist
```

运行日志：`/tmp/wordscan.log`（BLE）、`/tmp/tokenbridge.out`（桥接，出现 `UPLOAD_OK` 即成功）。

### 环境变量

| 变量 | 说明 |
|---|---|
| `WORDCARD_TARGET_UUID` | 目标设备 macOS peripheral UUID（必填） |
| `TOKEN_DISPLAY_INTERVAL` | 刷新间隔秒数，默认 20 |
| `WORDCARD_STATS` | 统计 JSON 路径，默认 `/tmp/tokenmeter.json` |
| `WORDCARD_FONT` | 面板字体路径 |

要显示自己的数据：实现一个守护进程写同格式 JSON，改 `bridge.py::render()` 即可。

## 已知限制

- 固件要求传输文件名**恰好 12 字符**；设备按文件名做 Flash 断点续传（跨重启保留）。
- 设备休眠后停止广播：按任意键唤醒，服务会自动重连认证。
- macOS 侧 CoreBluetooth 只能由 LaunchAgent 驱动（shell 直跑会静默卡死），详见踩坑文档。

## 协议逆向

`docs/protocol.md` 记录了 GATT 布局、双向认证握手（含密钥表来源）、FE 帧格式、
命令表与设备拉取式文件传输状态机。核心要点：

- 服务 `AE00`：`AE01` 写（≤244B/次）、`AE02` 通知；广播名 `YDW:<SN>`。
- 认证为双向挑战响应，成功互发 `02 pass`；加密为杰理私有分组密码。
- 文件声明 `0x1B`：4B 大小(BE) + 2B 整文件 CRC16/XMODEM + 12 字符文件名 + NUL。
- 设备用 `0x1D` ACK 指定"从哪个偏移读多少字节"；**最终 ACK(offset=0) 即完成**，
  再多发数据会触发校验失败（reason 02）。

## 免责声明与致谢

本项目为个人设备互操作性研究，仅供学习；与网易有道无任何关联，"有道""单词卡"
等商标归其所有者。逆向仅基于公开资料与自购设备的黑盒测试，不含任何厂商固件或专有代码。
请勿用于侵权用途，风险自担。

协议与认证实现参考了这些优秀的开源逆向（感谢）：

- [hybridherbst/web-bluetooth-e87](https://github.com/hybridherbst/web-bluetooth-e87)（MIT）— 杰理认证密码表与 E87 徽章抓包
- [jumpingmushroom/e87_badge](https://github.com/jumpingmushroom/e87_badge) — 真机 ae01/ae02 抓包与 jadx 笔记
- [MinatiScape/boat-crest](https://github.com/MinatiScape/boat-crest)、[auroraphtgrp01/smart-health](https://github.com/auroraphtgrp01/smart-health) — `jl_rcsp` 反编译源码

## License

MIT
