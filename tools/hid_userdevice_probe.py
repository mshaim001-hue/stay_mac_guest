#!/usr/bin/env python3
"""Probe IOHIDUserDeviceCreate + HandleReport vs school HIDSystemState idle."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import c_char_p, c_int, c_uint, c_uint8, c_void_p, POINTER

CFAllocatorRef = c_void_p
CFDictionaryRef = c_void_p
CFTypeRef = c_void_p
CFIndex = ctypes.c_long
IOReturn = c_int
IOHIDUserDeviceRef = c_void_p


def school_idle() -> float:
    js = (
        'ObjC.import("CoreGraphics");'
        "function s(t){ return $.CGEventSourceSecondsSinceLastEventType(1, t); }"
        "Math.floor(Math.min(s(1), s(3), s(10), s(22), s(25)));"
    )
    out = subprocess.check_output(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", js], text=True
    )
    return float(out.strip())


def hid_idle() -> float:
    out = subprocess.check_output(["/usr/sbin/ioreg", "-c", "IOHIDSystem"], text=True)
    for line in out.splitlines():
        if "HIDIdleTime" in line and "=" in line:
            return int(line.split("=")[-1].strip()) / 1e9
    return -1.0


# Boot keyboard descriptor
KBD_DESC = bytes(
    [
        0x05,
        0x01,
        0x09,
        0x06,
        0xA1,
        0x01,
        0x05,
        0x07,
        0x19,
        0xE0,
        0x29,
        0xE7,
        0x15,
        0x00,
        0x25,
        0x01,
        0x75,
        0x01,
        0x95,
        0x08,
        0x81,
        0x02,
        0x95,
        0x01,
        0x75,
        0x08,
        0x81,
        0x01,
        0x95,
        0x05,
        0x75,
        0x01,
        0x05,
        0x08,
        0x19,
        0x01,
        0x29,
        0x05,
        0x91,
        0x02,
        0x95,
        0x01,
        0x75,
        0x03,
        0x91,
        0x01,
        0x95,
        0x06,
        0x75,
        0x08,
        0x15,
        0x00,
        0x25,
        0x65,
        0x05,
        0x07,
        0x19,
        0x00,
        0x29,
        0x65,
        0x81,
        0x00,
        0xC0,
    ]
)


def main() -> int:
    cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

    # CF helpers
    cf.CFStringCreateWithCString.restype = CFTypeRef
    cf.CFStringCreateWithCString.argtypes = [CFAllocatorRef, c_char_p, c_uint]
    cf.CFDataCreate.restype = CFTypeRef
    cf.CFDataCreate.argtypes = [CFAllocatorRef, ctypes.POINTER(c_uint8), CFIndex]
    cf.CFNumberCreate.restype = CFTypeRef
    cf.CFNumberCreate.argtypes = [CFAllocatorRef, c_int, c_void_p]
    cf.CFDictionaryCreateMutable.restype = CFTypeRef
    cf.CFDictionaryCreateMutable.argtypes = [
        CFAllocatorRef,
        CFIndex,
        c_void_p,
        c_void_p,
    ]
    cf.CFDictionarySetValue.argtypes = [CFTypeRef, CFTypeRef, CFTypeRef]
    cf.CFRelease.argtypes = [CFTypeRef]

    kCFStringEncodingUTF8 = 0x08000100
    kCFNumberSInt32Type = 3

    def cfstr(s: str) -> CFTypeRef:
        return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)

    def cfnum(v: int) -> CFTypeRef:
        n = c_int(v)
        return cf.CFNumberCreate(None, kCFNumberSInt32Type, ctypes.byref(n))

    # IOHIDUserDevice
    iokit.IOHIDUserDeviceCreate.restype = IOHIDUserDeviceRef
    iokit.IOHIDUserDeviceCreate.argtypes = [CFAllocatorRef, CFDictionaryRef]
    iokit.IOHIDUserDeviceHandleReport.restype = IOReturn
    iokit.IOHIDUserDeviceHandleReport.argtypes = [
        IOHIDUserDeviceRef,
        ctypes.POINTER(c_uint8),
        CFIndex,
    ]

    print("=== IOHIDUserDevice probe ===")
    print("Wait 6s (don't touch input)...")
    time.sleep(6)
    before_s, before_h = school_idle(), hid_idle()
    print(f"BEFORE  school={before_s:.0f}  hid={before_h:.1f}")

    props = cf.CFDictionaryCreateMutable(None, 0, None, None)
    desc = (c_uint8 * len(KBD_DESC)).from_buffer_copy(KBD_DESC)
    desc_data = cf.CFDataCreate(None, desc, len(KBD_DESC))

    pairs = {
        "ReportDescriptor": desc_data,
        "VendorID": cfnum(0x05AC),
        "ProductID": cfnum(0xF00D),
        "Product": cfstr("StayMacGuestVirtualKbd"),
        "Manufacturer": cfstr("StayMacGuestProbe"),
        "PrimaryUsagePage": cfnum(0x01),
        "PrimaryUsage": cfnum(0x06),
    }
    # IOHIDKeys use specific constant strings — set both common forms
    key_map = {
        "ReportDescriptor": "ReportDescriptor",
        "VendorID": "VendorID",
        "ProductID": "ProductID",
        "Product": "Product",
        "Manufacturer": "Manufacturer",
        "PrimaryUsagePage": "PrimaryUsagePage",
        "PrimaryUsage": "PrimaryUsage",
    }
    for k, v in pairs.items():
        cf.CFDictionarySetValue(props, cfstr(key_map[k]), v)

    print("IOHIDUserDeviceCreate...")
    device = iokit.IOHIDUserDeviceCreate(None, props)
    if not device:
        print("RESULT: CREATE_FAILED (nil)")
        print("Likely blocked: entitlement com.apple.developer.hid.virtual.device")
        print("Verdict: FAIL — virtual HID unavailable to Guest/ad-hoc process")
        return 2

    print(f"CREATE_OK device=0x{device:x}")

    # key 'a' = 0x04
    down = (c_uint8 * 8)(0, 0, 0x04, 0, 0, 0, 0, 0)
    up = (c_uint8 * 8)(0, 0, 0, 0, 0, 0, 0, 0)
    kr1 = iokit.IOHIDUserDeviceHandleReport(device, down, 8)
    time.sleep(0.05)
    kr2 = iokit.IOHIDUserDeviceHandleReport(device, up, 8)
    print(f"HandleReport down={kr1} up={kr2} (0=success)")

    time.sleep(0.5)
    after_s, after_h = school_idle(), hid_idle()
    print(f"AFTER   school={after_s:.0f}  hid={after_h:.1f}")

    school_ok = after_s >= 0 and after_s < 3 and before_s >= 5
    hid_ok = after_h >= 0 and after_h < 3 and before_h >= 5
    # if before was already low, still report direction
    if before_s < 5:
        print("NOTE: before school idle was already low; re-run after sitting idle 10s")
    print(f"school reset? {after_s < 3 and after_s >= 0} (need before>=5 for proof)")
    print(f"hid reset? {after_h < 3 and after_h >= 0}")

    if school_ok:
        print("Verdict: SUCCESS — virtual HID resets school HIDSystemState")
        return 0
    if device and (kr1 == 0 or kr2 == 0):
        print("Verdict: FAIL — device/report may work, school idle NOT reset")
        return 1
    print("Verdict: FAIL — no school idle reset")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
