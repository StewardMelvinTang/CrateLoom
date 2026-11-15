// Experimental, read-only recognition probe; not part of the desktop app.
// Compile on native storage: xcrun swiftc scripts/shazam-pilot.swift -o /tmp/crateloom-shazam-pilot
// Use a 10–12 second mono PCM WAV at 16 kHz, 32 kHz, 44.1 kHz or 48 kHz.
// --self-test uses only a local reference catalog. The default queries Shazam.
import Foundation
import AVFoundation
import ShazamKit

func emit(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

final class Probe: NSObject, SHSessionDelegate {
    var result: [String: Any]?
    func session(_ session: SHSession, didFind match: SHMatch) {
        result = ["state": "matched", "items": match.mediaItems.map { item -> [String: Any] in
            var value: [String: Any] = ["title": item.title ?? "", "artist": item.artist ?? ""]
            if let url = item.artworkURL { value["artwork_url"] = url.absoluteString }
            if let url = item.appleMusicURL { value["apple_music_url"] = url.absoluteString }
            if let isrc = item.isrc { value["isrc"] = isrc }
            return value
        }, "version_verified": false]
    }
    func session(_ session: SHSession, didNotFindMatchFor signature: SHSignature, error: Error?) {
        if let error = error as NSError? {
            var details: [[String: Any]] = []
            var underlying = error.userInfo[NSUnderlyingErrorKey] as? NSError
            while let cause = underlying, details.count < 4 {
                details.append(["domain": cause.domain, "code": cause.code, "error": cause.localizedDescription])
                underlying = cause.userInfo[NSUnderlyingErrorKey] as? NSError
            }
            result = ["state": "error", "domain": error.domain, "code": error.code, "error": error.localizedDescription, "underlying": details]
        } else { result = ["state": "no_match", "version_verified": false] }
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let path = arguments.first, !path.hasPrefix("--") else {
    emit(["state": "error", "error": "Usage: crateloom-shazam-pilot excerpt.wav [--self-test]"])
    exit(2)
}
do {
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    guard file.processingFormat.channelCount == 1,
          [16000.0, 32000.0, 44100.0, 48000.0].contains(file.processingFormat.sampleRate),
          file.length > 0, file.length <= Int64(file.processingFormat.sampleRate * 15) else {
        emit(["state": "error", "error": "Provide 1–15 seconds of mono PCM audio at a supported sample rate."])
        exit(2)
    }
    let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
    try file.read(into: buffer)
    let generator = SHSignatureGenerator()
    try generator.append(buffer, at: nil)
    let signature = generator.signature()
    let session: SHSession
    if arguments.contains("--self-test") {
        let catalog = SHCustomCatalog()
        try catalog.addReferenceSignature(signature, representing: [SHMediaItem(properties: [.title: "Local self-match control"])])
        session = SHSession(catalog: catalog)
    } else { session = SHSession() }
    let probe = Probe()
    session.delegate = probe
    session.match(signature)
    let deadline = Date().addingTimeInterval(20)
    while probe.result == nil && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    let result = probe.result ?? ["state": "timeout", "error": "Recognition did not complete within 20 seconds."]
    emit(result)
    exit(result["state"] as? String == "error" || result["state"] as? String == "timeout" ? 1 : 0)
} catch {
    emit(["state": "error", "error": error.localizedDescription])
    exit(1)
}
