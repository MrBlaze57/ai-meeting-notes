import AppKit
import ApplicationServices
import AVFoundation
import CoreMedia
import ScreenCaptureKit

// All file writes happen on one serial audio queue. Timestamp gaps are padded
// with silence so the two sources remain aligned after interruptions/silence.
final class AudioTrack {
    let name: String
    let url: URL
    var file: AVAudioFile?
    var firstTime: Double?
    var receivedFrames: Int64 = 0
    let baseline: Double

    init(name: String, directory: URL, baseline: Double) {
        self.name = name
        self.url = directory.appendingPathComponent(name + ".caf")
        self.baseline = baseline
    }

    func append(_ buffer: AVAudioPCMBuffer, at timestamp: Double) throws {
        guard buffer.frameLength > 0 else { return }
        if file == nil {
            file = try AVAudioFile(forWriting: url, settings: buffer.format.settings,
                                   commonFormat: buffer.format.commonFormat, interleaved: buffer.format.isInterleaved)
            firstTime = timestamp.isFinite ? timestamp : baseline
        }
        guard let file = file, let firstTime = firstTime else { return }
        guard file.processingFormat == buffer.format else {
            throw NSError(domain: "MeetingRecorder", code: 4, userInfo: [NSLocalizedDescriptionKey: "The audio device format changed. Saved audio is retained; restart capture with the new device."])
        }
        let expected = Int64(max(0, (timestamp - firstTime) * buffer.format.sampleRate))
        var missing = expected - file.length
        // Ignore sub-buffer clock jitter; preserve meaningful pauses/gaps.
        if missing > Int64(buffer.format.sampleRate * 0.02) {
            while missing > 0 {
                let frames = AVAudioFrameCount(min(missing, 4096))
                guard let silence = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: frames) else { break }
                silence.frameLength = frames
                for region in UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList) {
                    if let data = region.mData { memset(data, 0, Int(region.mDataByteSize)) }
                }
                try file.write(from: silence)
                missing -= Int64(frames)
            }
        }
        try file.write(from: buffer)
        receivedFrames += Int64(buffer.frameLength)
    }

    var receipt: [String: Any] {
        ["file": url.lastPathComponent, "frames": receivedFrames,
         "start_seconds": max(0, (firstTime ?? baseline) - baseline),
         "duration_seconds": file.map { Double($0.length) / $0.processingFormat.sampleRate } ?? 0]
    }

    func close() { file = nil }
}

final class Recorder: NSObject, NSApplicationDelegate, SCStreamOutput, SCStreamDelegate {
    var directory: URL?
    var mode = "online"
    var maxMinutes = 180
    var stream: SCStream?
    var engine: AVAudioEngine?
    var timer: Timer?
    var statusItem: NSStatusItem?
    var window: NSWindow?
    var state = "idle"
    var message = ""
    var startTime = Date()
    var baseline = CMClockGetTime(CMClockGetHostTimeClock()).seconds
    var finishing = false
    var acceptingAudio = true // Access only on audioQueue.
    var processingTimer: Timer?
    var processingURL: URL?
    var processingLaunching = false
    var finalRecorderReceipt: Data?
    let audioQueue = DispatchQueue(label: "com.johnhernandez.meeting-notes.audio")
    var tracks: [String: AudioTrack] = [:]
    let titleLabel = NSTextField(labelWithString: "AI Meeting Notes")
    let detailLabel = NSTextField(wrappingLabelWithString: "")
    let durationLabel = NSTextField(labelWithString: "")
    var stopButton: NSButton?
    var detectionTimer: Timer?
    var detectionGate = DetectionGate()
    var detectionBusy = false
    var detectorLock: Int32 = -1
    var detectionEnabled = false
    let detectionQueue = DispatchQueue(label: "com.johnhernandez.meeting-notes.detect")
    var detectionCheckbox: NSButton?
    var accessibilityButton: NSButton?
    var lastDetectionStatus = "stopped"

    var pluginRoot: URL { Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent() }
    var cacheDirectory: URL {
        if let path = argument("--cache") { return URL(fileURLWithPath: path, isDirectory: true) }
        if let data = try? Data(contentsOf: pluginRoot.appendingPathComponent(".mcp.json")),
           let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let servers = json["mcpServers"] as? [String: Any], let server = servers["meeting-notes"] as? [String: Any],
           let env = server["env"] as? [String: String], let home = env["MEETING_NOTES_HOME"] {
            return URL(fileURLWithPath: home, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/AI Meeting Notes/cache")
    }

    func argument(_ name: String) -> String? {
        let args = CommandLine.arguments
        guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
        return args[index + 1]
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        mode = argument("--mode") ?? "online"
        maxMinutes = Int(argument("--max-minutes") ?? "180") ?? 180
        if let path = argument("--capture-dir") { directory = URL(fileURLWithPath: path, isDirectory: true) }
        setupWindow()
        if let path = argument("--test-stop") {
            // Synthetic finalized-audio fixture only. This branch never calls startCapture.
            directory = URL(fileURLWithPath: path, isDirectory: true)
            mode = "in_person"
            finish(error: nil)
            return
        }
        if let path = argument("--process-dir") {
            directory = URL(fileURLWithPath: path, isDirectory: true)
            finishing = true
            acceptingAudio = false
            state = "stopped"
            let button = NSButton(title: "Retry", target: self, action: #selector(retryProcessing))
            button.bezelStyle = .rounded
            button.frame = NSRect(x: 28, y: 24, width: 180, height: 34)
            window?.contentView?.addSubview(button)
            stopButton = button
            beginProcessing(retry: true)
            return
        }
        if directory == nil {
            titleLabel.stringValue = "Ready when you are"
            detailLabel.stringValue = "Ask Codex to record a meeting, or enable meeting detection below.\n\nDetection looks for active call controls in Zoom, Teams, and Google Meet. It asks before recording.\n\nAccessibility access is needed to detect calls."
            durationLabel.stringValue = "Recording has not started."
            let checkbox = NSButton(checkboxWithTitle: "Ask to record online meetings", target: self, action: #selector(toggleDetection))
            checkbox.frame = NSRect(x: 28, y: 24, width: 260, height: 30)
            window?.contentView?.addSubview(checkbox)
            detectionCheckbox = checkbox
            let permission = NSButton(title: "Enable Accessibility", target: self, action: #selector(requestDetectionAccess))
            permission.bezelStyle = .rounded
            permission.frame = NSRect(x: 292, y: 24, width: 155, height: 30)
            permission.isHidden = AXIsProcessTrusted()
            window?.contentView?.addSubview(permission)
            accessibilityButton = permission
            if CommandLine.arguments.contains("--watch") {
                let forced = CommandLine.arguments.contains("--enable-detection")
                checkbox.state = !forced && UserDefaults.standard.object(forKey: "meetingDetectionEnabled") as? Bool == false ? .off : .on
                if forced { UserDefaults.standard.set(true, forKey: "meetingDetectionEnabled") }
                if checkbox.state == .on { enableDetection(prompt: false) }
                if AXIsProcessTrusted() { window?.orderOut(nil); NSApp.setActivationPolicy(.accessory) }
            }
            return
        }
        guard ["online", "in_person"].contains(mode), (1...480).contains(maxMinutes) else {
            fail("Invalid capture mode or recording duration.")
            return
        }
        state = "starting"
        detailLabel.stringValue = "Preparing \(mode == "online" ? "microphone and Mac audio" : "microphone")…\nGrant access in the macOS permission prompts."
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.title = "◉ Preparing"
        let menu = NSMenu()
        let item = NSMenuItem(title: "Stop meeting recording", action: #selector(stopFromUI), keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        statusItem?.menu = menu
        let button = NSButton(title: "Stop recording", target: self, action: #selector(stopFromUI))
        button.bezelStyle = .rounded
        button.frame = NSRect(x: 28, y: 24, width: 180, height: 34)
        window?.contentView?.addSubview(button)
        stopButton = button
        persist()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        Task { await self.startCapture() }
    }

    func setupWindow() {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 470, height: 330),
                           styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        win.title = "AI Meeting Notes"
        win.isReleasedWhenClosed = false
        titleLabel.font = .systemFont(ofSize: 26, weight: .semibold)
        titleLabel.frame = NSRect(x: 28, y: 258, width: 414, height: 36)
        detailLabel.font = .systemFont(ofSize: 14)
        detailLabel.frame = NSRect(x: 28, y: 90, width: 414, height: 150)
        durationLabel.font = .monospacedDigitSystemFont(ofSize: 15, weight: .medium)
        durationLabel.frame = NSRect(x: 28, y: 60, width: 414, height: 24)
        win.contentView?.addSubview(titleLabel)
        win.contentView?.addSubview(detailLabel)
        win.contentView?.addSubview(durationLabel)
        win.center()
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = win
    }

    @MainActor func startCapture() async {
        do {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            guard granted else { throw captureError("Microphone access was denied. Enable MeetingRecorder in System Settings → Privacy & Security → Microphone, then retry.") }
            guard !finishing else { return }
            baseline = CMClockGetTime(CMClockGetHostTimeClock()).seconds
            startTime = Date()
            if mode == "online" {
                let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
                guard !finishing else { return }
                guard let display = content.displays.first else { throw captureError("No Mac display is available for system audio capture.") }
                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.captureMicrophone = true
                config.excludesCurrentProcessAudio = true
                config.sampleRate = 48000
                config.channelCount = 2
                config.width = 2
                config.height = 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                config.showsCursor = false
                // No screen output is installed; no images/video are stored.
                let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
                let activeStream = SCStream(filter: filter, configuration: config, delegate: self)
                stream = activeStream
                try activeStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
                try activeStream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: audioQueue)
                try await activeStream.startCapture()
            } else {
                let activeEngine = AVAudioEngine()
                engine = activeEngine
                let input = activeEngine.inputNode
                let format = input.outputFormat(forBus: 0)
                guard format.sampleRate > 0, format.channelCount > 0 else { throw captureError("No microphone is available. Connect/select a microphone and retry.") }
                input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, when in
                    guard let self = self else { return }
                    self.audioQueue.sync {
                        self.append(buffer, name: "microphone", timestamp: AVAudioTime.seconds(forHostTime: when.hostTime))
                    }
                }
                try activeEngine.start()
            }
            if finishing {
                try? await stream?.stopCapture()
                engine?.stop()
                return
            }
            state = "recording"
            titleLabel.stringValue = "Recording your meeting"
            detailLabel.stringValue = mode == "online" ? "Microphone + Mac system audio\n\nAudio from all Mac apps is included.\nUse headphones to reduce echo.\n\nClick Stop to save notes, transcript, and audio to Meetings automatically." : "Microphone only\n\nKeep your microphone near the conversation.\n\nClick Stop to save notes, transcript, and audio to Meetings automatically."
            statusItem?.button?.contentTintColor = .systemRed
            persist()
        } catch {
            fail(error.localizedDescription + (mode == "online" ? "\nFor Mac audio, check Privacy & Security → Screen & System Audio Recording, then relaunch." : ""))
        }
    }

    func captureError(_ message: String) -> NSError {
        NSError(domain: "MeetingRecorder", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    func append(_ buffer: AVAudioPCMBuffer, name: String, timestamp: Double) {
        guard acceptingAudio, let directory = directory else { return }
        do {
            if tracks[name] == nil { tracks[name] = AudioTrack(name: name, directory: directory, baseline: baseline) }
            try tracks[name]?.append(buffer, at: timestamp)
        } catch {
            DispatchQueue.main.async { self.fail(error.localizedDescription) }
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard sampleBuffer.isValid, [.audio, .microphone].contains(outputType) else { return }
        do {
            try sampleBuffer.withAudioBufferList { list, _ in
                guard let description = sampleBuffer.formatDescription?.audioStreamBasicDescription else { return }
                var asbd = description
                guard let format = AVAudioFormat(streamDescription: &asbd),
                      let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: list.unsafePointer) else { return }
                append(buffer, name: outputType == .audio ? "system" : "microphone", timestamp: sampleBuffer.presentationTimeStamp.seconds)
            }
        } catch { DispatchQueue.main.async { self.fail(error.localizedDescription) } }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { self.fail(error.localizedDescription) }
    }

    func tick() {
        guard let directory = directory, !finishing else { return }
        let seconds = Int(Date().timeIntervalSince(startTime))
        durationLabel.stringValue = state == "recording" ? String(format: "%02d:%02d elapsed", seconds / 60, seconds % 60) : "Waiting for permissions…"
        statusItem?.button?.title = state == "recording" ? String(format: "🔴 %02d:%02d", seconds / 60, seconds % 60) : "◉ Preparing"
        persist()
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("STOP").path) || (state == "recording" && seconds >= maxMinutes * 60) {
            stopFromUI()
        }
    }

    func persist() {
        guard let directory = directory else { return }
        let trackData = audioQueue.sync { tracks.mapValues { $0.receipt } }
        var result: [String: Any] = ["state": state, "pid": ProcessInfo.processInfo.processIdentifier,
                                   "heartbeat": Date().timeIntervalSince1970, "mode": mode,
                                   "duration_seconds": Date().timeIntervalSince(startTime), "tracks": trackData]
        if !message.isEmpty { result["error"] = message }
        do {
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            try data.write(to: directory.appendingPathComponent("recorder.json"), options: .atomic)
        } catch {
            // A write failure must end capture, otherwise the host cannot monitor/stop it.
            if !finishing { fail("Unable to save recording status: " + error.localizedDescription) }
        }
    }

    @objc func stopFromUI() { finish(error: nil) }
    func fail(_ message: String) { finish(error: message) }

    func finish(error: String?) {
        guard !finishing else { return }
        finishing = true
        // Reject subsequent buffers immediately, before waiting for async stream shutdown.
        audioQueue.sync { acceptingAudio = false }
        timer?.invalidate()
        stopButton?.isEnabled = false
        titleLabel.stringValue = "Stopping recording…"
        detailLabel.stringValue = "Closing audio files. Your notes will be saved to Meetings automatically."
        statusItem?.button?.contentTintColor = nil
        statusItem?.button?.title = "◷ Finishing"
        state = "stopping"
        persist()
        Task { @MainActor in
            if let activeStream = stream { try? await activeStream.stopCapture() }
            stream = nil
            if let engine = engine {
                engine.stop()
                engine.inputNode.removeTap(onBus: 0)
            }
            engine = nil
            // Capture receipts before closing AVAudioFile, then flush its headers.
            let savedTracks = audioQueue.sync { tracks.mapValues { $0.receipt } }
            audioQueue.sync { tracks.values.forEach { $0.close() } }
            state = error == nil ? "stopped" : "error"
            message = error ?? ""
            if let directory = directory {
                let result: [String: Any] = ["state": state, "pid": ProcessInfo.processInfo.processIdentifier,
                    "heartbeat": Date().timeIntervalSince1970, "mode": mode, "tracks": savedTracks,
                    "duration_seconds": Date().timeIntervalSince(startTime), "error": message]
                do {
                    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
                    finalRecorderReceipt = data
                    try data.write(to: directory.appendingPathComponent("recorder.json"), options: .atomic)
                    finalRecorderReceipt = nil
                } catch {
                    processingFailure("Recording stopped, but its final status could not be saved: " + error.localizedDescription)
                    return
                }
            }
            if let error = error {
                if let item = statusItem { NSStatusBar.system.removeStatusItem(item) }
                titleLabel.stringValue = "Recording needs attention"
                detailLabel.stringValue = error
                durationLabel.stringValue = "Any captured audio was retained."
                window?.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            } else { beginProcessing() }
        }
    }

    func beginProcessing(retry: Bool = false) {
        guard let directory = directory else { return }
        if let data = finalRecorderReceipt {
            do {
                try data.write(to: directory.appendingPathComponent("recorder.json"), options: .atomic)
                finalRecorderReceipt = nil
            } catch { processingFailure(error.localizedDescription); return }
        }
        if stopButton == nil {
            let button = NSButton(title: "Processing…", target: self, action: #selector(retryProcessing))
            button.bezelStyle = .rounded
            button.frame = NSRect(x: 28, y: 24, width: 180, height: 34)
            window?.contentView?.addSubview(button)
            stopButton = button
        }
        titleLabel.stringValue = "Recording stopped"
        detailLabel.stringValue = "Preparing your meeting notes…\n\nNotes, the full transcript, and recording will be saved to Meetings automatically. You can close this window."
        durationLabel.stringValue = "Your microphone and Mac audio capture are off."
        stopButton?.isEnabled = false
        stopButton?.title = "Processing…"
        processingLaunching = true
        if statusItem == nil { statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength) }
        statusItem?.button?.contentTintColor = nil
        statusItem?.button?.title = "◷ Meeting notes"
        let menu = NSMenu()
        let show = NSMenuItem(title: "Show meeting progress", action: #selector(showProcessing), keyEquivalent: "")
        show.target = self
        menu.addItem(show)
        let open = NSMenuItem(title: "Open Meetings", action: #selector(openNotes), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        menu.addItem(NSMenuItem(title: "Close progress app", action: #selector(NSApplication.terminate(_:)), keyEquivalent: ""))
        statusItem?.menu = menu
        processingTimer?.invalidate()
        processingTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.updateProcessing() }
        // Persist the dispatch marker before launch so MCP startup can recover a crash here.
        do { try Data().write(to: directory.appendingPathComponent("AUTO-PROCESS"), options: .atomic) }
        catch { processingFailure("Could not queue processing: " + error.localizedDescription); return }
        detectionQueue.async {
            let process = Process()
            let output = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", self.pluginRoot.appendingPathComponent("server/main.py").path,
                                 "--finish-meeting", "--meeting", directory.lastPathComponent,
                                 "--home", directory.deletingLastPathComponent().path] + (retry ? ["--retry"] : [])
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
            process.environment = env
            process.standardOutput = output
            process.standardError = output
            do {
                try process.run()
                let result = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                DispatchQueue.main.async {
                    self.processingLaunching = false
                    if process.terminationStatus != 0 {
                        self.processingFailure(String(decoding: result, as: UTF8.self))
                    } else { self.updateProcessing() }
                }
            } catch { DispatchQueue.main.async { self.processingLaunching = false; self.processingFailure(error.localizedDescription) } }
        }
    }

    @objc func retryProcessing() { beginProcessing(retry: true) }
    @objc func showProcessing() { window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func openNotes() {
        if let url = processingURL { NSWorkspace.shared.open(url); return }
        if let data = try? Data(contentsOf: cacheDirectory.appendingPathComponent("settings.json")),
           let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let value = config["meetings_page_url"] as? String, let url = URL(string: value) {
            NSWorkspace.shared.open(url)
        }
    }

    func processingFailure(_ error: String) {
        processingLaunching = false
        processingTimer?.invalidate()
        titleLabel.stringValue = "Recording saved"
        detailLabel.stringValue = "Notes could not finish. Your audio and completed steps are saved.\n\n" + String(error.prefix(500))
        durationLabel.stringValue = "Click Retry to finish automatically."
        stopButton?.title = "Retry"
        stopButton?.action = #selector(retryProcessing)
        stopButton?.isEnabled = true
        statusItem?.button?.title = "⚠︎ Meeting notes"
        showProcessing()
    }

    func updateProcessing() {
        guard !processingLaunching else { return }
        guard let directory = directory,
              let data = try? Data(contentsOf: directory.appendingPathComponent("finish-job.json")),
              let job = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let jobState = job["state"] as? String else { return }
        if jobState == "complete", let value = job["page_url"] as? String, let url = URL(string: value) {
            processingURL = url
            processingTimer?.invalidate()
            titleLabel.stringValue = "Saved to Meetings"
            detailLabel.stringValue = "Your meeting notes, full transcript, and recording are ready in Spaces."
            durationLabel.stringValue = "Recording is stopped."
            stopButton?.title = "Open notes"
            stopButton?.action = #selector(openNotes)
            stopButton?.isEnabled = true
            statusItem?.button?.title = "✓ Meeting saved"
        } else if jobState == "error" {
            processingFailure(job["error"] as? String ?? "Processing needs a retry.")
        } else {
            if let pid = job["pid"] as? Int32, kill(pid, 0) != 0,
               Date().timeIntervalSince1970 - (job["heartbeat"] as? Double ?? job["started_epoch"] as? Double ?? 0) > 10 {
                processingFailure("Processing was interrupted. Retry resumes the saved work.")
                return
            }
            let phase = job["phase"] as? String ?? "transcribe"
            let label = phase == "publish" ? "Saving to Meetings…" : phase == "notes" ? "Writing meeting notes…" : "Transcribing recording…"
            titleLabel.stringValue = "Recording stopped"
            detailLabel.stringValue = label + "\n\nNotes, transcript, and recording will be saved to Meetings automatically. You can close this window."
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if directory != nil && !finishing {
            stopFromUI()
            return .terminateCancel
        }
        return .terminateNow
    }

    @objc func toggleDetection() {
        let enabled = detectionCheckbox?.state == .on
        UserDefaults.standard.set(enabled, forKey: "meetingDetectionEnabled")
        if enabled { enableDetection(prompt: true) }
        else {
            detectionEnabled = false
            detectionTimer?.invalidate()
            saveDetectionStatus("disabled")
            if detectorLock >= 0 { close(detectorLock); detectorLock = -1 }
        }
    }

    func enableDetection(prompt: Bool) {
        try? FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
        if detectorLock == -1 {
            let fd = open(cacheDirectory.appendingPathComponent(".watcher-lock").path, O_CREAT | O_RDWR, 0o600)
            guard fd >= 0, flock(fd, LOCK_EX | LOCK_NB) == 0 else {
                if fd >= 0 { close(fd) }
                durationLabel.stringValue = "Meeting detection is already running."
                return
            }
            detectorLock = fd
        }
        if prompt {
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
        }
        detectionEnabled = true
        statusItem = statusItem ?? NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.title = "🎙"
        let menu = NSMenu()
        let settings = NSMenuItem(title: "Meeting detection settings", action: #selector(showDetectionSettings), keyEquivalent: "")
        settings.target = self
        menu.addItem(settings)
        let quit = NSMenuItem(title: "Quit meeting detection", action: #selector(quitDetection), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
        statusItem?.menu = menu
        detectionTimer?.invalidate()
        detectionTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.detectionTick() }
        detectionTick()
    }

    @objc func requestDetectionAccess() {
        detectionCheckbox?.state = .on
        UserDefaults.standard.set(true, forKey: "meetingDetectionEnabled")
        enableDetection(prompt: true)
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func showDetectionSettings() {
        NSApp.setActivationPolicy(.regular)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func quitDetection() {
        detectionEnabled = false
        UserDefaults.standard.set(false, forKey: "meetingDetectionEnabled")
        saveDetectionStatus("disabled")
        NSApp.terminate(nil)
    }

    func saveDetectionStatus(_ state: String) {
        lastDetectionStatus = state
        let value: [String: Any] = ["state": state, "pid": ProcessInfo.processInfo.processIdentifier,
                                  "heartbeat": Date().timeIntervalSince1970, "requires_accessibility": !AXIsProcessTrusted()]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: cacheDirectory.appendingPathComponent("watcher.json"), options: .atomic)
        }
    }

    func detectionTick() {
        guard detectionEnabled, !detectionBusy else { return }
        if FileManager.default.fileExists(atPath: cacheDirectory.appendingPathComponent("STOP-WATCHER").path) {
            UserDefaults.standard.set(false, forKey: "meetingDetectionEnabled")
            saveDetectionStatus("disabled")
            NSApp.terminate(nil)
            return
        }
        guard AXIsProcessTrusted() else {
            accessibilityButton?.isHidden = false
            durationLabel.stringValue = "Enable MeetingRecorder in Privacy & Security → Accessibility."
            saveDetectionStatus("needs_accessibility")
            return
        }
        accessibilityButton?.isHidden = true
        durationLabel.stringValue = "Meeting detection is on. Audio starts only after you choose Start."
        saveDetectionStatus("watching")
        detectionBusy = true
        detectionQueue.async {
            let candidates = MeetingAccessibility.scan()
            let active = self.hasActiveCapture()
            DispatchQueue.main.async {
                guard self.detectionEnabled else { self.detectionBusy = false; return }
                let event = self.detectionGate.observe(candidates, now: Date().timeIntervalSince1970, recording: active)
                if let event = event { self.offerRecording(event) }
                self.detectionBusy = false
            }
        }
    }

    func hasActiveCapture() -> Bool {
        guard let folders = try? FileManager.default.contentsOfDirectory(at: cacheDirectory, includingPropertiesForKeys: nil) else { return false }
        return folders.contains { folder in
            guard let data = try? Data(contentsOf: folder.appendingPathComponent("recorder.json")),
                  let info = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let state = info["state"] as? String, ["starting", "recording", "stopping"].contains(state) else { return false }
            if let pid = info["pid"] as? Int32 { return kill(pid, 0) == 0 }
            return Date().timeIntervalSince1970 - (info["heartbeat"] as? Double ?? 0) < 120
        }
    }

    func offerRecording(_ event: DetectedMeeting) {
        let alert = NSAlert()
        alert.messageText = "Record this \(event.provider) meeting?"
        alert.informativeText = "Your microphone and Mac system audio will be recorded. Click Stop when you’re done. Notes, the transcript, and recording will then be saved to Meetings in Spaces automatically."
        alert.addButton(withTitle: "Start recording")
        alert.addButton(withTitle: "Not now")
        alert.addButton(withTitle: "Disable detection")
        NSApp.activate(ignoringOtherApps: true)
        let choice = alert.runModal()
        if choice == .alertThirdButtonReturn {
            detectionCheckbox?.state = .off
            toggleDetection()
        } else if choice == .alertFirstButtonReturn {
            detectionQueue.async { self.startDetectedMeeting(event) }
        }
    }

    func startDetectedMeeting(_ event: DetectedMeeting) {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", pluginRoot.appendingPathComponent("server/main.py").path,
                             "--detected-start", String(event.title.prefix(180)), "--home", cacheDirectory.path]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        process.environment = env
        process.standardOutput = output
        process.standardError = output
        do {
            try process.run()
            let result = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            if process.terminationStatus != 0 {
                DispatchQueue.main.async {
                    let alert = NSAlert()
                    alert.messageText = "Could not start recording"
                    alert.informativeText = String(decoding: result, as: UTF8.self)
                    alert.runModal()
                }
            }
        } catch {
            DispatchQueue.main.async { self.durationLabel.stringValue = error.localizedDescription; self.showDetectionSettings() }
        }
    }
}

@main struct MeetingRecorderApp {
    static func main() {
        if let index = CommandLine.arguments.firstIndex(of: "--test-detection"), index + 1 < CommandLine.arguments.count {
            do { try DetectionFixtures.run(path: CommandLine.arguments[index + 1]) }
            catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
            return
        }
        let app = NSApplication.shared
        let recorder = Recorder()
        app.delegate = recorder
        withExtendedLifetime(recorder) { app.run() }
    }
}
