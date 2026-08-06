import AppKit
import Foundation
import IOKit

/// Menu-bar controller: launch once in the morning, runs all day.
/// Does NOT need Accessibility — the heavy lifting is stay_via_firefox.py
/// (jiggle via Firefox, which already has AX from Mosyle).

let idleThresholdMinutes = 10
let statusPollSeconds: TimeInterval = 5

func repoRootCandidates() -> [URL] {
    var urls: [URL] = []
    if let res = Bundle.main.resourceURL {
        urls.append(res)
        urls.append(res.deletingLastPathComponent()) // Contents/
        urls.append(res.deletingLastPathComponent().deletingLastPathComponent()) // .app
        urls.append(
            res.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        ) // dist/
        urls.append(
            res
                .deletingLastPathComponent().deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent()
        ) // repo root when app is in dist/
    }
    urls.append(URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Desktop/stay_mac_guest"))
    urls.append(URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
    return urls
}

func findStayScript() -> URL? {
    for root in repoRootCandidates() {
        let candidate = root.appendingPathComponent("stay_via_firefox.py")
        if FileManager.default.isReadableFile(atPath: candidate.path) {
            return candidate
        }
    }
    return nil
}

func hidIdleSeconds() -> Double {
    var iter = io_iterator_t()
    guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOHIDSystem"), &iter) == KERN_SUCCESS else {
        return -1
    }
    defer { IOObjectRelease(iter) }
    let entry = IOIteratorNext(iter)
    guard entry != 0 else { return -1 }
    defer { IOObjectRelease(entry) }
    var props: Unmanaged<CFMutableDictionary>?
    guard IORegistryEntryCreateCFProperties(entry, &props, kCFAllocatorDefault, 0) == KERN_SUCCESS,
          let dict = props?.takeRetainedValue() as? [String: Any],
          let ns = dict["HIDIdleTime"] as? UInt64 else {
        return -1
    }
    return Double(ns) / 1_000_000_000
}

func readDaemonStatus() -> [String: Any]? {
    let url = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Library/Logs/StayMacGuest-Firefox.status")
    guard let data = try? Data(contentsOf: url),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return nil
    }
    return obj
}

final class StayController: NSObject {
    private var statusItem: NSStatusItem?
    private var process: Process?
    private var pollTimer: Timer?
    private var scriptURL: URL?
    private var statusMenuItem: NSMenuItem?

    func start() {
        scriptURL = findStayScript()
        setupMenu()
        NSApp.setActivationPolicy(.accessory)

        if scriptURL == nil {
            setTitle("⏳?")
            statusMenuItem?.title = "Не найден stay_via_firefox.py"
            return
        }

        launchDaemon()
        pollTimer = Timer.scheduledTimer(withTimeInterval: statusPollSeconds, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
        refreshStatus()
    }

    private func setupMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        setTitle("⏳…")
        let menu = NSMenu()
        statusMenuItem = menu.addItem(withTitle: "Starting…", action: nil, keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Restart engine", action: #selector(restartDaemon), keyEquivalent: "r")
        menu.addItem(withTitle: "Open log", action: #selector(openLog), keyEquivalent: "l")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Quit StayMacGuest", action: #selector(quit), keyEquivalent: "q")
        menu.items.forEach { $0.target = self }
        statusItem?.menu = menu
        statusItem?.button?.toolTip = "StayMacGuest — один запуск на весь день Guest"
    }

    private func setTitle(_ text: String) {
        statusItem?.button?.title = text
    }

    private func launchDaemon() {
        guard let script = scriptURL else { return }
        stopDaemon()

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script.path]
        p.currentDirectoryURL = script.deletingLastPathComponent()
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self else { return }
                if self.process === proc {
                    self.setTitle("⏳✗")
                    self.statusMenuItem?.title = "Engine stopped (exit \(proc.terminationStatus))"
                }
            }
        }
        do {
            try p.run()
            process = p
            setTitle("⏳")
            statusMenuItem?.title = "Engine starting…"
        } catch {
            setTitle("⏳!")
            statusMenuItem?.title = "Failed to start: \(error.localizedDescription)"
        }
    }

    private func stopDaemon() {
        guard let p = process, p.isRunning else {
            process = nil
            return
        }
        p.terminate()
        // Give python a moment to SIGTERM its Firefox group
        let deadline = Date().addingTimeInterval(2)
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if p.isRunning {
            p.interrupt()
        }
        process = nil
    }

    private func refreshStatus() {
        let idle = hidIdleSeconds()
        let idleMin = idle / 60.0
        if let st = readDaemonStatus(),
           let state = st["state"] as? String {
            let idleShown = (st["idle_sec"] as? Double) ?? idle
            switch state {
            case "watching":
                setTitle("⏳")
                statusMenuItem?.title = String(
                    format: "Смотрю · idle %.0f мин (порог %d) · работаешь — jiggle не нужен",
                    idleShown / 60.0,
                    idleThresholdMinutes
                )
            case "jiggling":
                setTitle("⏳!")
                statusMenuItem?.title = "Jiggle сейчас…"
            case "armed":
                setTitle("⏳✓")
                statusMenuItem?.title = String(
                    format: "На страже · idle %.0f мин · jiggle если ≥ %d мин",
                    idleShown / 60.0,
                    idleThresholdMinutes
                )
            case "error":
                setTitle("⏳✗")
                let extra = st["extra"] as? String ?? "error"
                statusMenuItem?.title = "Ошибка: \(extra)"
            case "stopped":
                setTitle("⏳·")
                statusMenuItem?.title = "Остановлен"
            default:
                setTitle("⏳")
                statusMenuItem?.title = "\(state) · idle \(String(format: "%.0f", idleMin)) мин"
            }
        } else if process?.isRunning == true {
            setTitle("⏳…")
            statusMenuItem?.title = String(format: "Starting… idle %.0f мин", idleMin)
        } else {
            setTitle("⏳?")
            statusMenuItem?.title = String(format: "Нет статуса · idle %.0f мин", idleMin)
        }
    }

    @objc private func restartDaemon() {
        launchDaemon()
    }

    @objc private func openLog() {
        let log = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Logs/StayMacGuest-Firefox.log")
        NSWorkspace.shared.open(log)
    }

    @objc private func quit() {
        stopDaemon()
        // Also ask python via pid file if still around
        let pidURL = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Logs/StayMacGuest-Firefox.pid")
        if let text = try? String(contentsOf: pidURL, encoding: .utf8),
           let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)),
           pid > 0 {
            kill(pid, SIGTERM)
        }
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let controller = StayController()
controller.start()
app.run()
