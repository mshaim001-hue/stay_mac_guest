import Foundation
import IOKit
import IOKit.hid

/// Probe: can Guest create IOHIDUserDevice and does it reset school idle metric?
/// School metric = CGEventSourceSecondsSinceLastEventType(HIDSystemState, click/key/scroll).

func schoolIdle() -> Double {
    let js = """
    ObjC.import("CoreGraphics");
    function s(t){ return $.CGEventSourceSecondsSinceLastEventType(1, t); }
    Math.floor(Math.min(s(1), s(3), s(10), s(22), s(25)));
    """
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-l", "JavaScript", "-e", js]
    let out = Pipe()
    p.standardOutput = out
    p.standardError = FileHandle.nullDevice
    try? p.run()
    p.waitUntilExit()
    let data = out.fileHandleForReading.readDataToEndOfFile()
    return Double(String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "") ?? -1
}

func hidIdle() -> Double {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/ioreg")
    p.arguments = ["-c", "IOHIDSystem"]
    let out = Pipe()
    p.standardOutput = out
    p.standardError = FileHandle.nullDevice
    try? p.run()
    p.waitUntilExit()
    let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    for line in text.split(separator: "\n") where line.contains("HIDIdleTime") && line.contains("=") {
        if let n = UInt64(line.split(separator: "=").last!.trimmingCharacters(in: .whitespaces)) {
            return Double(n) / 1_000_000_000
        }
    }
    return -1
}

// Boot keyboard report descriptor (8-byte input reports)
let kbdDescriptor: [UInt8] = [
    0x05, 0x01, // Usage Page (Generic Desktop)
    0x09, 0x06, // Usage (Keyboard)
    0xA1, 0x01, // Collection (Application)
    0x05, 0x07, //   Usage Page (Key Codes)
    0x19, 0xE0, //   Usage Minimum (224)
    0x29, 0xE7, //   Usage Maximum (231)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x01, //   Logical Maximum (1)
    0x75, 0x01, //   Report Size (1)
    0x95, 0x08, //   Report Count (8)
    0x81, 0x02, //   Input (Data, Var, Abs) ; modifier byte
    0x95, 0x01, //   Report Count (1)
    0x75, 0x08, //   Report Size (8)
    0x81, 0x01, //   Input (Const) ; reserved
    0x95, 0x05, //   Report Count (5)
    0x75, 0x01, //   Report Size (1)
    0x05, 0x08, //   Usage Page (LEDs)
    0x19, 0x01, //   Usage Minimum (1)
    0x29, 0x05, //   Usage Maximum (5)
    0x91, 0x02, //   Output (Data, Var, Abs)
    0x95, 0x01, //   Report Count (1)
    0x75, 0x03, //   Report Size (3)
    0x91, 0x01, //   Output (Const)
    0x95, 0x06, //   Report Count (6)
    0x75, 0x08, //   Report Size (8)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x65, //   Logical Maximum (101)
    0x05, 0x07, //   Usage Page (Key Codes)
    0x19, 0x00, //   Usage Minimum (0)
    0x29, 0x65, //   Usage Maximum (101)
    0x81, 0x00, //   Input (Data, Array)
    0xC0, // End Collection
]

print("=== IOHIDUserDevice probe ===")
print("Wait 6s idle (don't touch keyboard/mouse)...")
Thread.sleep(forTimeInterval: 6)
let beforeSchool = schoolIdle()
let beforeHid = hidIdle()
print(String(format: "BEFORE  school=%.0f  hid=%.1f", beforeSchool, beforeHid))

let descData = Data(kbdDescriptor) as CFData
var vendor: Int32 = 0x05AC // Apple-ish dummy
var product: Int32 = 0xF00D
var usagePage: Int32 = 0x01
var usage: Int32 = 0x06

let props: [String: Any] = [
    kIOHIDReportDescriptorKey: descData,
    kIOHIDVendorIDKey: vendor,
    kIOHIDProductIDKey: product,
    kIOHIDProductKey: "StayMacGuestVirtualKbd",
    kIOHIDManufacturerKey: "StayMacGuestProbe",
    kIOHIDPrimaryUsagePageKey: usagePage,
    kIOHIDPrimaryUsageKey: usage,
]

print("IOHIDUserDeviceCreate...")
guard let device = IOHIDUserDeviceCreate(kCFAllocatorDefault, props as CFDictionary) else {
    print("RESULT: CREATE_FAILED (nil)")
    print("Likely needs entitlement com.apple.developer.hid.virtual.device")
    print("Verdict: virtual HID not available to ad-hoc Guest binary → not a path for Stay")
    exit(2)
}

print("CREATE_OK device=\(device)")

// Boot keyboard report: modifier, reserved, 6 keycodes. 0x04 = 'a'
var reportDown: [UInt8] = [0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00]
var reportUp: [UInt8] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

let kr1 = reportDown.withUnsafeMutableBufferPointer { buf -> IOReturn in
    IOHIDUserDeviceHandleReport(device, buf.baseAddress, buf.count)
}
Thread.sleep(forTimeInterval: 0.05)
let kr2 = reportUp.withUnsafeMutableBufferPointer { buf -> IOReturn in
    IOHIDUserDeviceHandleReport(device, buf.baseAddress, buf.count)
}
print(String(format: "HandleReport down=%d up=%d (0=success)", kr1, kr2))

Thread.sleep(forTimeInterval: 0.4)
let afterSchool = schoolIdle()
let afterHid = hidIdle()
print(String(format: "AFTER   school=%.0f  hid=%.1f", afterSchool, afterHid))

let schoolReset = afterSchool >= 0 && afterSchool < 3
let hidReset = afterHid >= 0 && afterHid < 3
print("school reset?", schoolReset)
print("hid reset?", hidReset)

if schoolReset {
    print("Verdict: SUCCESS — virtual HID resets school HIDSystemState")
    exit(0)
} else if kr1 == 0 || kr2 == 0 {
    print("Verdict: device created & report accepted, but school idle NOT reset")
    exit(1)
} else {
    print("Verdict: create ok-ish but reports failed / no school reset")
    exit(1)
}
