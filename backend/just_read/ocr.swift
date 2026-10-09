import Foundation
import PDFKit
import AppKit
import Vision

struct OCRPage: Codable {
    let page: Int
    let text: String
    let confidence: Double
    let error: String?
}
let args = CommandLine.arguments
if args.count < 3 { exit(2) }
guard let document = PDFDocument(url: URL(fileURLWithPath: args[1])),
      let data = args[2].data(using: .utf8),
      let pages = try? JSONDecoder().decode([Int].self, from: data) else { exit(2) }
var output: [OCRPage] = []
for number in pages {
    autoreleasepool {
        guard let page = document.page(at: number - 1) else { return }
        let thumbnail = page.thumbnail(of: NSSize(width: 1800, height: 2400), for: .mediaBox)
        guard let image = thumbnail.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
            let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first }
            let text = lines.map { $0.string }.joined(separator: "\n")
            let confidence = lines.isEmpty ? 0 : lines.reduce(0.0) { $0 + Double($1.confidence) } / Double(lines.count)
            output.append(OCRPage(page: number, text: text, confidence: confidence, error: nil))
        } catch { output.append(OCRPage(page: number, text: "", confidence: 0, error: String(describing: error))) }
    }
}
if let encoded = try? JSONEncoder().encode(output) { FileHandle.standardOutput.write(encoded) }
