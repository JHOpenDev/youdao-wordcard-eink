import Foundation
import CoreBluetooth

// BLE 侦察代理 v3: 常驻扫描; 通过 /tmp/wordscan.target 控制连接目标(写 UUID 字符串)
// 全部广播与 GATT 交互写入 /tmp/wordscan.log
let LOG = "/tmp/wordscan.log"
let TARGET_FILE = "/tmp/wordscan.target"
let WRITE_FILE = "/tmp/wordscan.write"
let fh = FileHandle(forWritingAtPath: LOG) ?? { FileManager.default.createFile(atPath: LOG, contents: nil); return FileHandle(forWritingAtPath: LOG)! }()
fh.seekToEndOfFile()
func log(_ s: String) {
    let d = Data("[\(Int(Date().timeIntervalSince1970))] ".utf8) + (s + "\n").data(using: .utf8)!
    fh.write(d); fh.synchronizeFile()
}

final class C: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var cm: CBCentralManager!
    var seen: Set<UUID> = []
    var wantTarget: UUID? = nil
    var connecting: Set<UUID> = []
    var periphs: [UUID: CBPeripheral] = [:]
    var activePeriph: CBPeripheral?
    var writeChar: CBCharacteristic?

    override init() { super.init(); cm = CBCentralManager(delegate: self, queue: .main) }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        log("STATE=\(c.state.rawValue)")
        if c.state == .poweredOn { c.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]) }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi: NSNumber) {
        let id = p.identifier
        let name = p.name ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? "(nil)"
        let svcs = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID])?.map(\.uuidString).joined(separator: ",") ?? "-"
        let mfg = advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data
        let mfgHex = mfg.map { "0x" + $0.map { String(format: "%02x", $0) }.joined() } ?? "-"
        let svcData = advertisementData[CBAdvertisementDataServiceDataKey] as? [CBUUID: Data]
        let svcDataStr = svcData.map { $0.map { "\($0.key.uuidString)=0x" + $0.value.map { String(format: "%02x", $0) }.joined() }.joined(separator: ",") } ?? "-"
        if !seen.contains(id) {
            seen.insert(id)
            log("DISCOVER id=\(id.uuidString) rssi=\(rssi) name=\(name) svcs=\(svcs) mfg=\(mfgHex) svcData=\(svcDataStr)")
            periphs[id] = p
        }
        // 目标匹配: 指定 UUID，或任何 YDW 前缀广播
        let isNamedYDW = name.uppercased().hasPrefix("YDW:")
        if ((wantTarget == id) || isNamedYDW) && !connecting.contains(id) {
            log("[*] connect target \(id) name=\(name)")
            connecting.insert(id); p.delegate = self; c.connect(p, options: nil)
        }
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        log("CONNECTED \(p.identifier.uuidString) name=\(p.name ?? "nil")")
        p.discoverServices(nil)
    }
    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        log("CONNECT_FAIL \(error.map(String.init(describing:)) ?? "nil")"); connecting.remove(p.identifier)
    }
    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        log("DISCONNECTED \(error.map(String.init(describing:)) ?? "clean")"); connecting.remove(p.identifier)
    }
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        log("SERVICES \(error.map(String.init(describing:)) ?? "ok")")
        for s in p.services ?? [] { log("SERVICE \(s.uuid.uuidString)"); p.discoverCharacteristics(nil, for: s) }
    }
    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        log("CHARS svc=\(s.uuid.uuidString) \(error.map(String.init(describing:)) ?? "ok")")
        for c in s.characteristics ?? [] {
            if c.uuid.uuidString == "AE01" { activePeriph = p; writeChar = c }
            var pr: [String] = []
            if c.properties.contains(.read) { pr.append("R") }
            if c.properties.contains(.write) { pr.append("W") }
            if c.properties.contains(.writeWithoutResponse) { pr.append("WNR") }
            if c.properties.contains(.notify) { pr.append("N") }
            if c.properties.contains(.indicate) { pr.append("I") }
            log("CHAR \(c.uuid.uuidString) props=\(pr.joined(separator: "|")) mtu_nr=\(p.maximumWriteValueLength(for: .withoutResponse)) mtu_w=\(p.maximumWriteValueLength(for: .withResponse))")
            if c.properties.contains(.read) { p.readValue(for: c) }
            if c.properties.contains(.notify) || c.properties.contains(.indicate) { p.setNotifyValue(true, for: c) }
        }
    }
    func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
        let d = c.value ?? Data()
        log("VALUE \(c.uuid.uuidString) err=\(error.map(String.init(describing:)) ?? "nil") hex=0x\(d.map { String(format: "%02x", $0) }.joined()) utf8=\(String(data: d, encoding: .utf8) ?? "-")")
    }
    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) {
        log("NOTIFY_STATE \(c.uuid.uuidString) err=\(error.map(String.init(describing:)) ?? "ok")")
    }
}

log("=== agent v3 start ===")
log("AUTH=\(CBCentralManager.authorization.rawValue)")
let c = C()
var lastTarget: String? = nil
var lastWrite: String? = nil
Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { _ in
    let t = try? String(contentsOfFile: TARGET_FILE, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    let w = try? String(contentsOfFile: WRITE_FILE, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    if let w, !w.isEmpty, w != lastWrite {
        lastWrite = w
        let hex = w.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "0x", with: "")
        var data = Data(); var i = hex.startIndex
        while i < hex.endIndex, let j = hex.index(i, offsetBy: 2, limitedBy: hex.endIndex) {
            if let b = UInt8(hex[i..<j], radix: 16) { data.append(b) }
            i = j
        }
        if let p = c.activePeriph, let ch = c.writeChar {
            log("TX AE01 len=\(data.count) hex=0x\(data.map { String(format: "%02x", $0) }.joined())")
            p.writeValue(data, for: ch, type: .withoutResponse)
        } else { log("TX_DROP no active AE01") }
    }
    if t != lastTarget {
        lastTarget = t
        if let t, let u = UUID(uuidString: t) {
            log("[ctrl] target set: \(t)")
            c.wantTarget = u
            if let p = c.periphs[u] {
                c.connecting.insert(u); p.delegate = c; c.cm.connect(p, options: nil)
            } else {
                let known = c.cm.retrievePeripherals(withIdentifiers: [u])
                log("[ctrl] retrieve known peripherals: \(known.count)")
                for p in known { c.periphs[u] = p; c.connecting.insert(u); p.delegate = c; c.cm.connect(p, options: nil) }
            }
        } else if t == nil || t?.isEmpty == true {
            c.wantTarget = nil
        }
    }
}
RunLoop.main.run(until: Date().addingTimeInterval(86400))
log("=== agent end ===")
exit(0)
