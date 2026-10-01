import AppKit
import ApplicationServices
import Foundation

struct MeetingSnapshot: Codable {
    var bundleID: String
    var title: String
    var url: String?
    var controls: [String]
}

struct DetectedMeeting: Codable {
    var key: String
    var provider: String
    var title: String
}

enum MeetingRules {
    static let nativeApps = Set(["us.zoom.xos", "com.microsoft.teams", "com.microsoft.teams2"])
    static let browsers = Set(["com.google.Chrome", "com.apple.Safari", "com.microsoft.edgemac", "company.thebrowser.Browser", "org.mozilla.firefox"])

    static func detect(_ snapshot: MeetingSnapshot) -> DetectedMeeting? {
        let labels = snapshot.controls.map { $0.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) }
        let hasLeave = labels.contains { label in
            ["leave", "leave call", "leave meeting", "end", "end meeting", "end call", "hang up"].contains(label)
            || ["leave call (", "leave meeting (", "end meeting (", "hang up ("].contains { label.hasPrefix($0) }
        }
        let hasMic = labels.contains { $0.contains("microphone") || $0.contains("mute") || $0 == "mic" || $0.hasPrefix("mic ") }
        guard hasLeave && hasMic else { return nil }
        if snapshot.bundleID == "us.zoom.xos" {
            return DetectedMeeting(key: "zoom:" + snapshot.title, provider: "Zoom", title: snapshot.title.isEmpty ? "Zoom meeting" : snapshot.title)
        }
        if snapshot.bundleID == "com.microsoft.teams" || snapshot.bundleID == "com.microsoft.teams2" {
            return DetectedMeeting(key: "teams:" + snapshot.title, provider: "Microsoft Teams", title: snapshot.title.isEmpty ? "Teams meeting" : snapshot.title)
        }
        guard browsers.contains(snapshot.bundleID), let raw = snapshot.url else { return nil }
        let url = URLComponents(string: raw.hasPrefix("http") ? raw : "https://" + raw)
        guard let host = url?.host?.lowercased() else { return nil }
        let path = url?.path ?? ""
        let provider: String
        if host == "meet.google.com", path.range(of: "^/[a-z]{3}-[a-z]{4}-[a-z]{3}/?$", options: .regularExpression) != nil {
            provider = "Google Meet"
        } else if host == "teams.microsoft.com" || host == "teams.live.com" {
            provider = "Microsoft Teams"
        } else if (host == "zoom.us" || host.hasSuffix(".zoom.us")), (path.contains("/wc/") || path.contains("/join")) {
            provider = "Zoom"
        } else { return nil }
        return DetectedMeeting(key: host + path, provider: provider, title: snapshot.title.isEmpty ? provider + " meeting" : snapshot.title)
    }
}

// Requires two positive observations; dismissal is remembered for this meeting.
// Brief focus/Accessibility failures do not produce repeat prompts.
struct DetectionGate {
    var sightings: [String: Int] = [:]
    var lastSeen: [String: Double] = [:]
    var dismissed = Set<String>()
    var lastPrompt = -Double.infinity

    mutating func observe(_ candidates: [DetectedMeeting], now: Double, recording: Bool) -> DetectedMeeting? {
        let keys = Set(candidates.map { $0.key })
        for key in Array(lastSeen.keys) where !keys.contains(key) {
            sightings[key] = 0
            if now - (lastSeen[key] ?? now) > 45 {
                lastSeen.removeValue(forKey: key)
                sightings.removeValue(forKey: key)
                dismissed.remove(key)
            }
        }
        var counted = Set<String>()
        for candidate in candidates where counted.insert(candidate.key).inserted {
            lastSeen[candidate.key] = now
            sightings[candidate.key, default: 0] += 1
        }
        if recording { candidates.forEach { dismissed.insert($0.key) } }
        guard !recording, now - lastPrompt >= 20 else { return nil }
        guard let candidate = candidates.first(where: { sightings[$0.key, default: 0] >= 2 && !dismissed.contains($0.key) }) else { return nil }
        dismissed.insert(candidate.key)
        lastPrompt = now
        return candidate
    }
}

enum MeetingAccessibility {
    static func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &result) == .success else { return nil }
        return result
    }

    static func scan() -> [DetectedMeeting] {
        guard AXIsProcessTrusted() else { return [] }
        let apps = NSWorkspace.shared.runningApplications.filter {
            guard let bundle = $0.bundleIdentifier else { return false }
            return MeetingRules.nativeApps.contains(bundle) || MeetingRules.browsers.contains(bundle)
        }
        var candidates: [DetectedMeeting] = []
        for app in apps {
            let element = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(element, 0.2)
            guard let windows = value(element, kAXWindowsAttribute) as? [AXUIElement] else { continue }
            // Inspect selected visible windows, not browser history or inactive tabs.
            for window in windows.prefix(12) {
                if (value(window, kAXMinimizedAttribute) as? Bool) == true { continue }
                let title = value(window, kAXTitleAttribute) as? String ?? ""
                var url = value(window, kAXDocumentAttribute) as? String
                var labels: [String] = []
                var queue: [(AXUIElement, Int)] = [(window, 0)]
                var index = 0
                let deadline = Date().addingTimeInterval(0.7)
                while index < queue.count && index < 2500 && Date() < deadline {
                    let (node, depth) = queue[index]
                    index += 1
                    let role = value(node, kAXRoleAttribute) as? String ?? ""
                    if role == "AXWebArea", let webURL = value(node, kAXURLAttribute) as? URL { url = webURL.absoluteString }
                    if role == kAXTextFieldRole, let candidate = value(node, kAXValueAttribute) as? String,
                       candidate.range(of: "^(https?://)?(meet\\.google\\.com|teams\\.(microsoft|live)\\.com|[a-z0-9.-]*zoom\\.us)/", options: .regularExpression) != nil {
                        url = candidate
                    }
                    if [kAXButtonRole, kAXCheckBoxRole, kAXPopUpButtonRole, "AXToggleButton"].contains(role),
                       (value(node, "AXHidden") as? Bool) != true {
                        for attribute in [kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute] {
                            if let label = value(node, attribute) as? String, label.count < 160 { labels.append(label) }
                        }
                    }
                    if depth < 18, let children = value(node, kAXChildrenAttribute) as? [AXUIElement] {
                        queue.append(contentsOf: children.prefix(500).map { ($0, depth + 1) })
                    }
                }
                let snapshot = MeetingSnapshot(bundleID: app.bundleIdentifier ?? "", title: title, url: url, controls: labels)
                if let detected = MeetingRules.detect(snapshot) { candidates.append(detected) }
            }
        }
        return candidates
    }
}

// CLI-only behavioral fixtures, intentionally run before NSApplication starts.
struct DetectionStep: Codable { var snapshots: [MeetingSnapshot]; var time: Double; var recording: Bool }
enum DetectionFixtures {
    static func run(path: String) throws {
        let steps = try JSONDecoder().decode([DetectionStep].self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        var gate = DetectionGate()
        let results: [DetectedMeeting?] = steps.map { step in
            gate.observe(step.snapshots.compactMap(MeetingRules.detect), now: step.time, recording: step.recording)
        }
        let output = try JSONEncoder().encode(results)
        print(String(decoding: output, as: UTF8.self))
    }
}
