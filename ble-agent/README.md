# BLE Agent

CoreBluetooth 代理，经 launchd 在 GUI 会话运行（shell 直跑 CoreBluetooth 会静默卡死，
见 `../docs/macos-ble-pitfalls.md`）。需要独立 .app bundle 携带蓝牙用途声明。

构建：

```bash
mkdir -p WordScan.app/Contents/MacOS
cat > WordScan.app/Contents/Info.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.wordscan</string>
<key>CFBundleName</key><string>WordScan</string>
<key>CFBundleExecutable</key><string>wordscan</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1.0</string>
<key>NSBluetoothAlwaysUsageDescription</key><string>连接自有墨水屏设备推送数据</string>
<key>NSBluetoothPeripheralUsageDescription</key><string>连接自有墨水屏设备</string>
</dict></plist>
PLIST
swiftc -O agent.swift -o WordScan.app/Contents/MacOS/wordscan
codesign --force --deep -s - WordScan.app
```

控制接口：

- `/tmp/wordscan.target` — 写入目标 peripheral UUID（macOS 侧稳定标识，非 MAC）
- `/tmp/wordscan.write` — 每行一段 hex，代理按 244B 分片写入 AE01
- `/tmp/wordscan.log` — 全部广播/连接/GATT 收发日志（追加写）

代理自动连接广播名以 `YDW:` 开头的设备；修改 `agent.swift` 中 `isNamedYDW`
可适配其他广播名。
