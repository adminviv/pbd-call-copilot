// pbd-syscap: streams what the Mac is playing (the other people on the Zoom call)
// to stdout as 16 kHz mono float32, using Apple's ScreenCaptureKit (macOS 13+).
// Nothing joins the call. Exits when stdin closes, so it never outlives the app.
//
// Build: swiftc -O -o pbd-syscap syscap.swift
// First run asks for "Screen & System Audio Recording" permission.
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

final class Capturer: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let out = FileHandle.standardOutput

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "pbd-syscap", code: 1, userInfo: [NSLocalizedDescriptionKey: "no display"])
        }
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 16000
        config.channelCount = 1
        // Audio only: keep the (required) video side tiny and slow.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        let queue = DispatchQueue(label: "pbd-syscap")
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)  // silences "output NOT found"
        try await stream.startCapture()
        self.stream = stream
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let block = sampleBuffer.dataBuffer else { return }
        let length = CMBlockBufferGetDataLength(block)
        var data = Data(count: length)
        let status = data.withUnsafeMutableBytes { raw in
            CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: length, destination: raw.baseAddress!)
        }
        if status == kCMBlockBufferNoErr { out.write(data) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write("stopped: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(2)
    }
}

// Without permission macOS sends silence, not an error, so check first (and ask once).
if !CGPreflightScreenCaptureAccess() {
    CGRequestScreenCaptureAccess()
    FileHandle.standardError.write("error: no screen & system audio recording permission\n".data(using: .utf8)!)
    exit(3)
}

let capturer = Capturer()
Task {
    do {
        try await capturer.start()
        FileHandle.standardError.write("capturing\n".data(using: .utf8)!)
    } catch {
        FileHandle.standardError.write("error: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(1)
    }
}
// Stop when the app that started us goes away.
Thread.detachNewThread {
    while !FileHandle.standardInput.availableData.isEmpty {}
    exit(0)
}
dispatchMain()
