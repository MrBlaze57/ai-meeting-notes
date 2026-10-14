import AVFoundation
import Foundation

@main struct RecordingUIFixture {
    static func main() throws {
        let input = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: Any]
        let channels = input["samples"] as! [[Double]]
        let frames = channels.first?.count ?? 0
        let interleaved = input["interleaved"] as? Bool ?? false
        let kind = input["format"] as? String ?? "float32"
        let common: AVAudioCommonFormat = kind == "int16" ? .pcmFormatInt16 : kind == "int32" ? .pcmFormatInt32 : .pcmFormatFloat32
        let format = AVAudioFormat(commonFormat: common, sampleRate: 48000, channels: AVAudioChannelCount(channels.count), interleaved: interleaved)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(max(1, frames)))!
        buffer.frameLength = AVAudioFrameCount(frames)
        let regions = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        for channel in 0..<channels.count {
            for frame in 0..<frames {
                let region = interleaved ? regions[0] : regions[channel]
                let index = interleaved ? frame * channels.count + channel : frame
                let value = channels[channel][frame]
                switch common {
                case .pcmFormatInt16:
                    region.mData!.assumingMemoryBound(to: Int16.self)[index] = Int16(max(-32768, min(32767, value * 32768)))
                case .pcmFormatInt32:
                    region.mData!.assumingMemoryBound(to: Int32.self)[index] = Int32(max(-2147483648, min(2147483647, value * 2147483648)))
                default:
                    let sample = input["invalid_samples"] as? Bool == true ? (frame % 2 == 0 ? Float.nan : Float.infinity) : Float(value)
                    region.mData!.assumingMemoryBound(to: Float.self)[index] = sample
                }
            }
        }
        let meter = MicrophoneMeter()
        meter.update(buffer, at: 10)
        if input["reset"] as? Bool == true { meter.reset() }
        let output = ["level": meter.level(at: input["poll_at"] as? Double ?? 10)]
        print(String(decoding: try JSONSerialization.data(withJSONObject: output), as: UTF8.self))
    }
}
