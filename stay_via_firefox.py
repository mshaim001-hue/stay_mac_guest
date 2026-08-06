#!/usr/bin/env python3
"""
All-day Guest stay daemon (no admin password).

Launch once in the morning:
  - Mosyle AutoLogOutDelay (~30 min): jiggle HID via Firefox Developer Edition
  - Tomorrow School idle-logout.sh (60 min): block its osascript idle probe
    (if the probe returns non-numeric, the script exits without logging out)

Runs until you quit the app/script or log out of Guest yourself.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Profile in Application Support so it works when script lives inside .app/Resources
SUPPORT = Path.home() / "Library/Application Support/StayMacGuest"
PROFILE = SUPPORT / "ff-stay-profile"
LOG = Path.home() / "Library/Logs/StayMacGuest-Firefox.log"
PID_FILE = Path.home() / "Library/Logs/StayMacGuest-Firefox.pid"
STATUS_FILE = Path.home() / "Library/Logs/StayMacGuest-Firefox.status"
# Mosyle TCC Accessibility is granted to Firefox Developer Edition only
# (org.mozilla.firefoxdeveloperedition). Regular Firefox.app must NOT be used —
# CGEventPost without AX does not reset AutoLogOutDelay idle.
FIREFOX_DEV = Path(
    "/Applications/Firefox Developer Edition.app/Contents/MacOS/firefox"
)
FIREFOX_REGULAR = Path("/Applications/Firefox.app/Contents/MacOS/firefox")
MARIONETTE_PORT = 2828
# Autologout ≈ 1800s. Act well before that; jiggle earlier with margin.
IDLE_THRESHOLD_SEC = 5 * 60
POLL_EVERY_SEC = 20
STARTUP_TIMEOUT_SEC = 60
# School LaunchDaemon runs idle-logout.sh every 15s; probe lasts ~50ms.
SCHOOL_PROBE_KILL_EVERY_SEC = 0.05
# pkill -f uses ERE — avoid $ () etc. School JS has "(1, t); }", ours uses "(1, x)".
SCHOOL_PROBE_PATTERN = "1, t); }"
SCHOOL_DIALOG_PATTERN = "Автовыход"

MINIMIZE_SCRIPT = r"""
const wm = Cc["@mozilla.org/appshell/window-mediator;1"].getService(Ci.nsIWindowMediator);
const win = wm.getMostRecentWindow("navigator:browser") || window;
if (win) {
  try { win.minimize(); } catch (e) {}
}
return "minimized";
"""

JIGGLE_SCRIPT = r"""
const wm = Cc["@mozilla.org/appshell/window-mediator;1"].getService(Ci.nsIWindowMediator);
const win = wm.getMostRecentWindow("navigator:browser") || window;
if (!win) {
  throw new Error("no browser window");
}
const utils = win.windowUtils;
const baseX = Math.round((win.screenX + 120) * win.devicePixelRatio);
const baseY = Math.round((win.screenY + 120) * win.devicePixelRatio);
const el = win.document.documentElement;

function move(x, y) {
  return new Promise((resolve, reject) => {
    try {
      utils.sendNativeMouseEvent(
        x,
        y,
        utils.NATIVE_MOUSE_MESSAGE_MOVE,
        0,
        0,
        el,
        () => resolve(true)
      );
    } catch (e) {
      reject(e);
    }
  });
}

return move(baseX + 1, baseY).then(() => move(baseX, baseY)).then(() => "jiggled");
"""

JIGGLE_SCRIPT_CTYPES = r"""
const { ctypes } = ChromeUtils.importESModule("resource://gre/modules/ctypes.sys.mjs");
const cg = ctypes.open("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics");
const cf = ctypes.open("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation");

const CGEventRef = ctypes.voidptr_t;
const CGPoint = ctypes.StructType("CGPoint", [
  { x: ctypes.double },
  { y: ctypes.double },
]);
const CGEventCreate = cg.declare(
  "CGEventCreate",
  ctypes.default_abi,
  CGEventRef,
  ctypes.voidptr_t
);
const CGEventGetLocation = cg.declare(
  "CGEventGetLocation",
  ctypes.default_abi,
  CGPoint,
  CGEventRef
);
const CGEventCreateMouseEvent = cg.declare(
  "CGEventCreateMouseEvent",
  ctypes.default_abi,
  CGEventRef,
  ctypes.voidptr_t,
  ctypes.uint32_t,
  CGPoint,
  ctypes.uint32_t
);
const CGEventCreateKeyboardEvent = cg.declare(
  "CGEventCreateKeyboardEvent",
  ctypes.default_abi,
  CGEventRef,
  ctypes.voidptr_t,
  ctypes.uint16_t,
  ctypes.bool
);
const CGEventPost = cg.declare(
  "CGEventPost",
  ctypes.default_abi,
  ctypes.void_t,
  ctypes.uint32_t,
  CGEventRef
);
const CFRelease = cf.declare(
  "CFRelease",
  ctypes.default_abi,
  ctypes.void_t,
  ctypes.voidptr_t
);

const kCGEventMouseMoved = 5;
const kCGHIDEventTap = 0;
const kCGMouseButtonLeft = 0;
// F18 — almost never bound. School idle-logout.sh watches KeyDown (not MouseMoved).
const kKeyF18 = 79;

const curEv = CGEventCreate(null);
const loc = CGEventGetLocation(curEv);
CFRelease(curEv);

function postMove(x, y) {
  const pt = CGPoint();
  pt.x = x;
  pt.y = y;
  const ev = CGEventCreateMouseEvent(null, kCGEventMouseMoved, pt, kCGMouseButtonLeft);
  CGEventPost(kCGHIDEventTap, ev);
  CFRelease(ev);
}

function postKey() {
  const down = CGEventCreateKeyboardEvent(null, kKeyF18, true);
  const up = CGEventCreateKeyboardEvent(null, kKeyF18, false);
  CGEventPost(kCGHIDEventTap, down);
  CGEventPost(kCGHIDEventTap, up);
  CFRelease(down);
  CFRelease(up);
}

// Mouse move → Mosyle AutoLogOutDelay / IOHID (~30 min).
postMove(loc.x + 1, loc.y);
postMove(loc.x, loc.y);
// KeyDown → Tomorrow School /usr/local/school/idle-logout.sh (THRESHOLD=3600).
postKey();
cg.close();
cf.close();
return "jiggled-ctypes+key";
"""


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%S") + "  " + msg
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_status(state: str, idle: float, extra: str = "") -> None:
    payload = {
        "state": state,
        "idle_sec": round(idle, 1),
        "threshold_sec": IDLE_THRESHOLD_SEC,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "extra": extra,
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def hid_idle_seconds() -> float:
    try:
        out = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem"], text=True, stderr=subprocess.DEVNULL
        )
    except Exception:
        return -1.0
    for line in out.splitlines():
        if "HIDIdleTime" in line and "=" in line:
            ns = int(line.split("=")[-1].strip())
            return ns / 1_000_000_000
    return -1.0


def school_idle_seconds() -> float:
    """Same metric family as idle-logout.sh, but different JS shape so the
    probe-blocker pkill pattern does not kill our own measurement.
    """
    js = (
        'ObjC.import("CoreGraphics");'
        "(() => { const xs = [1,3,10,22,25].map("
        "x => $.CGEventSourceSecondsSinceLastEventType(1, x)); "
        "return Math.floor(Math.min(...xs)); })()"
    )
    try:
        out = subprocess.check_output(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", js],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return float(out.strip())
    except Exception:
        return -1.0


def kill_school_osascripts() -> int:
    """Kill Guest osascripts used by idle-logout.sh (probe + warn dialog).

    idle-logout.sh does: idle=$(osascript …); [[ "$idle" =~ ^[0-9]+$ ]] || exit 0
    Empty/killed probe ⇒ no WARN, no launchctl bootout.
    """
    killed = 0
    for pattern in (SCHOOL_PROBE_PATTERN, SCHOOL_DIALOG_PATTERN):
        try:
            r = subprocess.run(
                ["pkill", "-9", "-f", pattern],
                capture_output=True,
                text=True,
            )
            # pkill: 0 = matched, 1 = no match
            if r.returncode == 0:
                killed += 1
        except OSError:
            pass
    return killed


def start_school_probe_blocker(stop_event: threading.Event) -> threading.Thread:
    stats = {"kills": 0, "ticks": 0}

    def loop() -> None:
        log(
            "school-blocker ON: глушим osascript замер "
            f"ai.tomorrowschool.idlelogout (каждые {SCHOOL_PROBE_KILL_EVERY_SEC}с)"
        )
        last_report = time.time()
        while not stop_event.is_set():
            stats["ticks"] += 1
            n = kill_school_osascripts()
            if n:
                stats["kills"] += n
            now = time.time()
            if now - last_report >= 120:
                log(
                    f"school-blocker heartbeat ticks={stats['ticks']} "
                    f"kill-waves={stats['kills']}"
                )
                last_report = now
                stats["ticks"] = 0
                stats["kills"] = 0
            stop_event.wait(SCHOOL_PROBE_KILL_EVERY_SEC)
        log("school-blocker OFF")

    t = threading.Thread(target=loop, name="school-probe-blocker", daemon=True)
    t.start()
    return t


def find_firefox() -> Path:
    if FIREFOX_DEV.is_file():
        return FIREFOX_DEV
    if FIREFOX_REGULAR.is_file():
        raise SystemExit(
            "Найден только обычный Firefox.app — у него нет Accessibility в Mosyle.\n"
            "Нужен Firefox Developer Edition (org.mozilla.firefoxdeveloperedition).\n"
            "Иначе jiggle не сбросит idle и Guest разлогинит через ~30 мин."
        )
    raise SystemExit(
        "Firefox Developer Edition не найден в /Applications.\n"
        "Он обязателен: только у него Accessibility в профиле Mosyle."
    )


class Marionette:
    def __init__(self, host: str = "127.0.0.1", port: int = MARIONETTE_PORT):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(90)
        self.buf = b""
        self._id = 0
        hello = self._recv()
        if not (isinstance(hello, dict) and "marionetteProtocol" in hello):
            raise RuntimeError(f"unexpected Marionette hello: {hello!r}")
        self._command("WebDriver:NewSession", {"capabilities": {}})
        self._command("Marionette:SetContext", {"value": "chrome"})

    def _recv(self):
        while b":" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Marionette connection closed")
            self.buf += chunk
        length_b, rest = self.buf.split(b":", 1)
        length = int(length_b)
        while len(rest) < length:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Marionette connection closed")
            rest += chunk
        raw, self.buf = rest[:length], rest[length:]
        return json.loads(raw.decode("utf-8"))

    def _send_packet(self, packet):
        data = json.dumps(packet).encode("utf-8")
        self.sock.sendall(f"{len(data)}:".encode("ascii") + data)
        return self._recv()

    def _command(self, name: str, params: dict | None = None):
        self._id += 1
        msg_id = self._id
        resp = self._send_packet([0, msg_id, name, params or {}])
        if not (isinstance(resp, list) and len(resp) >= 4):
            raise RuntimeError(f"{name}: bad response {resp!r}")
        error, result = resp[2], resp[3]
        if error:
            raise RuntimeError(f"{name} failed: {error}")
        return result

    def execute(self, script: str):
        return self._command(
            "WebDriver:ExecuteScript",
            {
                "script": script,
                "args": [],
                "newSandbox": True,
                "sandbox": "default",
            },
        )

    def close(self):
        try:
            self._command("WebDriver:DeleteSession", {})
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def wait_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def prepare_profile() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    (PROFILE / "user.js").write_text(
        "\n".join(
            [
                'user_pref("marionette.enabled", true);',
                f'user_pref("marionette.port", {MARIONETTE_PORT});',
                'user_pref("browser.shell.checkDefaultBrowser", false);',
                'user_pref("browser.startup.homepage", "about:blank");',
                'user_pref("startup.homepage_welcome_url", "about:blank");',
                'user_pref("startup.homepage_welcome_url.additional", "about:blank");',
                'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
                'user_pref("toolkit.telemetry.enabled", false);',
                'user_pref("app.update.enabled", false);',
                "",
            ]
        ),
        encoding="utf-8",
    )


def kill_stale_stay_firefox() -> None:
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    except Exception:
        return
    profile_token = str(PROFILE)
    for line in out.splitlines():
        if profile_token not in line:
            continue
        try:
            pid = int(line.strip().split(None, 1)[0])
        except ValueError:
            continue
        log(f"killing stale stay-Firefox pid={pid}")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(1.0)


def start_firefox(firefox: Path) -> subprocess.Popen:
    prepare_profile()
    kill_stale_stay_firefox()
    cmd = [
        str(firefox),
        "-marionette",
        "-remote-allow-system-access",
        "-no-remote",
        "-profile",
        str(PROFILE),
        "-foreground",
        "about:blank",
    ]
    env = os.environ.copy()
    env["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"] = "1"
    log("starting: " + " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def ensure_engine(firefox: Path, proc, client):
    """Return (proc, client), restarting Firefox/Marionette if needed."""
    alive = proc is not None and proc.poll() is None
    if alive and client is not None:
        return proc, client

    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(1)

    proc = start_firefox(firefox)
    if not wait_port(MARIONETTE_PORT, STARTUP_TIMEOUT_SEC):
        raise RuntimeError("Marionette port не открылся")
    client = Marionette(port=MARIONETTE_PORT)
    try:
        client.execute(MINIMIZE_SCRIPT)
    except Exception as e:
        log(f"minimize skipped: {e}")
    return proc, client


def jiggle(client: Marionette, *, require_proof: bool = True) -> str:
    before_hid = hid_idle_seconds()
    before_school = school_idle_seconds()
    errors = []
    result = None
    method = None
    for name, script in (("ctypes", JIGGLE_SCRIPT_CTYPES), ("windowUtils", JIGGLE_SCRIPT)):
        try:
            result = client.execute(script)
            method = name
            break
        except Exception as e:
            errors.append(f"{name}: {e}")
            log(f"jiggle method {name} failed: {e}")
    if method is None:
        raise RuntimeError("both jiggle methods failed: " + " | ".join(errors))
    time.sleep(0.5)
    after_hid = hid_idle_seconds()
    after_school = school_idle_seconds()
    # Mosyle path = HID. School 60‑min path is handled by probe-blocker, not jiggle.
    hid_ok = after_hid >= 0 and after_hid < 3.0
    if before_hid >= 5.0:
        ok = hid_ok
        proof = "hid-proven" if ok else "hid-FAIL"
    else:
        ok = True
        proof = "skip-proof (hid already low)"
    log(
        f"jiggle/{method} result={result!r} "
        f"hid {before_hid:.1f}→{after_hid:.1f}s "
        f"school {before_school:.0f}→{after_school:.0f}s "
        f"{proof}"
    )
    if require_proof and not ok:
        raise RuntimeError(
            "HID idle did not reset — нужен Accessibility у Firefox Developer Edition"
        )
    return str(result)


def claim_pid() -> None:
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            # process exists
            raise SystemExit(
                f"Уже запущено (pid {old}). Сначала Quit в меню ⏳ или: kill {old}"
            )
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def release_pid() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def main() -> int:
    firefox = find_firefox()
    claim_pid()
    log(f"Firefox: {firefox}")
    log("bundle: org.mozilla.firefoxdeveloperedition (нужен Accessibility в Mosyle)")
    log(f"Log: {LOG}")
    log(
        f"Режим: следим за idle; если ≥ {IDLE_THRESHOLD_SEC // 60} мин бездействия "
        f"→ jiggle (политика logout ≈ 30 мин). Один запуск на весь день."
    )

    proc = None
    client = None
    last_heartbeat = 0.0
    stop_blocker = threading.Event()
    blocker_thread = None

    def shutdown(*_args):
        log("stopping…")
        stop_blocker.set()
        write_status("stopped", hid_idle_seconds())
        try:
            if client:
                client.close()
        except Exception:
            pass
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        release_pid()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start school blocker ASAP — before Firefox bring-up — so a 15s tick can't win.
    blocker_thread = start_school_probe_blocker(stop_blocker)

    try:
        proc, client = ensure_engine(firefox, proc, client)
        # May not prove reset if user just launched (idle already ~0).
        jiggle(client, require_proof=False)
        write_status("armed", hid_idle_seconds(), "startup jiggle + school-blocker")
    except Exception as e:
        log(f"startup failed: {e}")
        write_status("error", hid_idle_seconds(), str(e))
        stop_blocker.set()
        release_pid()
        return 1

    if "--once" in sys.argv:
        log("--once: success, exiting")
        shutdown()
        return 0

    log(
        f"готово на весь день: HID-jiggle если idle ≥ {IDLE_THRESHOLD_SEC // 60} мин; "
        "school 60мин — блокировка osascript-замера"
    )

    while True:
        time.sleep(POLL_EVERY_SEC)
        idle = hid_idle_seconds()
        # school metric is informational; blocker is the real countermeasure
        school = school_idle_seconds()
        now = time.time()
        try:
            if proc.poll() is not None:
                log("Firefox умер — перезапуск")
                proc, client = ensure_engine(firefox, None, None)

            if idle < 0:
                write_status("error", idle, "cannot read HIDIdleTime")
                continue

            if now - last_heartbeat >= 120:
                log(
                    f"heartbeat hid={idle:.0f}s school≈{school:.0f}s "
                    f"threshold={IDLE_THRESHOLD_SEC}s blocker=on"
                )
                last_heartbeat = now

            if idle < IDLE_THRESHOLD_SEC:
                write_status(
                    "watching",
                    idle,
                    f"hid={idle:.0f} school≈{school:.0f} blocker=on",
                )
                continue

            log(f"idle hid={idle:.0f}s ≥ {IDLE_THRESHOLD_SEC}s → jiggle (Mosyle)")
            write_status("jiggling", idle)
            try:
                jiggle(client, require_proof=True)
                write_status("armed", hid_idle_seconds(), "jiggle ok + blocker")
            except Exception as e:
                log(f"jiggle error: {e}; reconnecting")
                proc, client = ensure_engine(firefox, None, None)
                jiggle(client, require_proof=True)
                write_status("armed", hid_idle_seconds(), "jiggle ok after reconnect")
        except Exception as e:
            log(f"loop error: {e}")
            write_status("error", idle, str(e))
            time.sleep(5)
            try:
                proc, client = ensure_engine(firefox, None, None)
            except Exception as e2:
                log(f"restart failed: {e2}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
