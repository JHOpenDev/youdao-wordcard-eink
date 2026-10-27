# macOS BLE / launchd 陷阱实录（全部实测踩过）

## 1. shell 进程里 CoreBluetooth 静默卡死

**现象**：在 ZCode Bash（无论是否 dangerouslyDisableSandbox）直接运行 swift/python 的
CoreBluetooth 代码，`centralManagerDidUpdateState` 永远不回调，`CBCentralManager.state`
停在 raw=0 (unknown)，授权值 `CBCentralManager.authorization` 却显示 3 (allowedAlways)。
不报错、不弹窗、XPC 无日志。裸 CLI 二进制、打包 .app 从 shell 启动，全部一样。

**解法**（三者缺一不可）：
1. 二进制放进 `.app` bundle，Info.plist 带 `NSBluetoothAlwaysUsageDescription`
   （`NSBluetoothPeripheralUsageDescription` 也加上）。
2. 通过 **launchd LaunchAgent**（`~/Library/LaunchAgents/*.plist`）在 GUI 会话拉起，
   不要由 shell/终端直接 exec。
3. `RunAtLoad` + `KeepAlive`，输出重定向到日志文件；轮询/控制用文件接口
   （本部署用 `/tmp/wordscan.target` 指定连接目标、`/tmp/wordscan.write` 下发 hex 帧）。

**验证蓝牙栈是否正常**：看日志出现 `STATE=5`（poweredOn）和 `DISCOVER ...` 行。
权限真被拒时回调会触发 raw=4 (unauthorized)——如果连 4 都没有，就是踩了本坑。

## 2. launchd 进程读不了 ~/Documents

**现象**：launchd 拉起的 python 卡在启动阶段，`sample` 显示阻塞在 `__open`，
`/tmp/*.out` 空文件。前台手动运行同一脚本完全正常。

**原因**：macOS TCC 对 `~/Documents` 等受保护目录的访问按"进程是否经用户交互启动"判定，
launchd 后台代理无权读取。

**解法**：运行时脚本和依赖全部放 `~/`（如 `/Users/YOU/wordcard-eink/`），Documents 里只留研究材料。

## 3. 日志覆盖写 vs 消费方 offset

**现象**：代理重启后桥接全部"认证超时"，但日志里明明有设备响应。

**原因**：FileHandle 默认从文件开头写（覆盖），而桥接按上次读取位置（offset）增量消费。
代理重启覆盖后，新内容写在旧 offset 之前，桥接永远读不到。

**解法**：代理每次 `log()` 前 `fh.seekToEndOfFile()`（追加模式）。
桥接侧 `Channel.lines()` 已有 size<pos 时归零的兜底，但这只处理截断，不处理覆盖。

## 4. 写入去重导致认证挑战被吞

代理通过比较 `/tmp/wordscan.write` 内容变化来下发帧：**内容与上次相同的写会被忽略**。
Jieli 认证的第一个挑战若用固定值（如 `00 01 02 ...`），连续两次认证就会第二次被吞。
**认证挑战必须每次 `os.urandom(16)`。** 重发相同数据帧时也要在末尾塞一个变化字节或先写别的行。

## 5. 其他实测事实

- 设备广播名 `YDW:<SN>`，广播不带服务 UUID，mfg 数据 `0xd605 02 005f 00 <MAC 反序>...`；
  macOS 侧用稳定 identifier（UUID）连接，`retrievePeripherals(withIdentifiers:)` 常返回空，
  必须等广播出现再 connect（约 40–90 秒一轮广播窗口）。
- 设备重启用杰理命令 0xE7（参数 0，非 OTA 模式），会断连；代理 KeepAlive 自动重连。
- 墨水屏只有内容变化才值得推送：bridge 对比本次与上次 JPEG 字节，相同则 sleep 跳过。
- 多次故障转移后设备可能停止广播直到按键唤醒——这是正常休眠行为，不是故障。
