import AppKit
import AVFoundation

// Shared between capture callbacks and the UI. No audio samples are retained.
final class MicrophoneMeter {
    private let lock = NSLock()
    private var measured: Float = 0
    private var sampledAt: TimeInterval?

    func update(_ buffer: AVAudioPCMBuffer, at time: TimeInterval = ProcessInfo.processInfo.systemUptime) {
        var squares = 0.0
        var count = 0
        func add(_ value: Double) {
            let sample = value.isFinite ? max(-1, min(1, value)) : 0
            squares += sample * sample
            count += 1
        }
        for region in UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList) {
            guard let data = region.mData else { continue }
            let frames = Int(buffer.frameLength) * Int(region.mNumberChannels)
            switch buffer.format.commonFormat {
            case .pcmFormatFloat32:
                let samples = data.assumingMemoryBound(to: Float.self)
                for index in 0..<min(frames, Int(region.mDataByteSize) / MemoryLayout<Float>.stride) { add(Double(samples[index])) }
            case .pcmFormatFloat64:
                let samples = data.assumingMemoryBound(to: Double.self)
                for index in 0..<min(frames, Int(region.mDataByteSize) / MemoryLayout<Double>.stride) { add(samples[index]) }
            case .pcmFormatInt16:
                let samples = data.assumingMemoryBound(to: Int16.self)
                for index in 0..<min(frames, Int(region.mDataByteSize) / MemoryLayout<Int16>.stride) { add(Double(samples[index]) / 32768) }
            case .pcmFormatInt32:
                let samples = data.assumingMemoryBound(to: Int32.self)
                for index in 0..<min(frames, Int(region.mDataByteSize) / MemoryLayout<Int32>.stride) { add(Double(samples[index]) / 2147483648) }
            default: break
            }
        }
        let rms = count > 0 ? sqrt(squares / Double(count)) : 0
        // A logarithmic scale makes quiet speech readable; silence stays flat.
        let level = rms > 0.001 ? Float(max(0, min(1, (20 * log10(rms) + 60) / 60))) : 0
        lock.lock()
        measured = level
        sampledAt = time
        lock.unlock()
    }

    func level(at time: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Float {
        lock.lock()
        defer { lock.unlock() }
        guard let sampledAt = sampledAt, time - sampledAt < 0.35 else { return 0 }
        return measured
    }

    func reset() {
        lock.lock()
        measured = 0
        sampledAt = nil
        lock.unlock()
    }
}

final class MicrophoneWaveform: NSView {
    private var history = Array(repeating: Float(0), count: 24)

    override init(frame: NSRect) {
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.levelIndicator)
        setAccessibilityLabel("Microphone sound level")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(_ level: Float) {
        history.removeFirst()
        history.append(max(0, min(1, level)))
        setAccessibilityValue(level > 0 ? "Microphone receiving sound" : "Quiet")
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        let spacing = bounds.width / CGFloat(history.count)
        for (index, level) in history.enumerated() {
            let height = 2 + CGFloat(level) * (bounds.height - 2)
            let rect = NSRect(x: CGFloat(index) * spacing, y: (bounds.height - height) / 2,
                              width: max(2, spacing - 2), height: height)
            NSColor.white.withAlphaComponent(0.35 + Double(index) / Double(history.count) * 0.6).setFill()
            NSBezierPath(roundedRect: rect, xRadius: 1.5, yRadius: 1.5).fill()
        }
    }
}

private final class IndicatorSurface: NSVisualEffectView {
    override var mouseDownCanMoveWindow: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
}

final class RecordingIndicator: NSPanel {
    private let elapsedLabel = NSTextField(labelWithString: "00:00")
    private let waveform = MicrophoneWaveform(frame: NSRect(x: 110, y: 20, width: 106, height: 24))
    private var stop: (() -> Void)?
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    init(preview: Bool = false, stop: @escaping () -> Void) {
        self.stop = stop
        super.init(contentRect: NSRect(x: 0, y: 0, width: 284, height: 64),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        title = preview ? "Recording indicator preview" : "Meeting recording"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        hidesOnDeactivate = false
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        appearance = NSAppearance(named: .darkAqua)
        let surface = IndicatorSurface(frame: NSRect(x: 0, y: 0, width: 284, height: 64))
        surface.material = .hudWindow
        surface.blendingMode = .behindWindow
        surface.state = .active
        surface.wantsLayer = true
        surface.layer?.cornerRadius = 18
        surface.layer?.masksToBounds = true
        contentView = surface

        let dot = NSImageView(frame: NSRect(x: 13, y: 36, width: 8, height: 8))
        dot.image = NSImage(systemSymbolName: "circle.fill", accessibilityDescription: nil)
        dot.contentTintColor = preview ? .systemGray : .systemRed
        surface.addSubview(dot)
        let status = NSTextField(labelWithString: preview ? "Preview" : "Recording")
        status.font = .systemFont(ofSize: 12, weight: .semibold)
        status.textColor = .white
        status.frame = NSRect(x: 28, y: 32, width: 76, height: 18)
        surface.addSubview(status)
        elapsedLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        elapsedLabel.textColor = .secondaryLabelColor
        elapsedLabel.frame = NSRect(x: 28, y: 14, width: 76, height: 18)
        surface.addSubview(elapsedLabel)
        surface.addSubview(waveform)
        let separator = NSBox(frame: NSRect(x: 229, y: 16, width: 1, height: 32))
        separator.boxType = .separator
        surface.addSubview(separator)
        let button = NSButton(image: NSImage(systemSymbolName: "stop.fill", accessibilityDescription: "Stop recording")!,
                              target: self, action: #selector(stopClicked))
        button.frame = NSRect(x: 241, y: 16, width: 30, height: 32)
        button.bezelStyle = .circular
        button.contentTintColor = .systemRed
        button.toolTip = preview ? "Close preview" : "Stop recording and save to Meetings"
        button.setAccessibilityLabel(preview ? "Close preview" : "Stop recording")
        surface.addSubview(button)
        if let frame = NSScreen.main?.visibleFrame {
            setFrameOrigin(NSPoint(x: max(frame.minX + 16, frame.maxX - 304), y: frame.minY + 24))
        }
    }

    func update(seconds: Int, level: Float) {
        let seconds = max(0, seconds)
        elapsedLabel.stringValue = String(format: "%02d:%02d", seconds / 60, seconds % 60)
        waveform.update(level)
    }

    @objc private func stopClicked() { stop?() }
}
