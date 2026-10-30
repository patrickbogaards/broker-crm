import Foundation
import AppKit
import PDFKit
import Vision

func ocr(_ image: CGImage) -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["de-DE", "en-US"]
    do {
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    } catch {
        fputs("Texterkennung fehlgeschlagen: \(error.localizedDescription)\n", stderr)
        return ""
    }
}

guard CommandLine.arguments.count == 2 else { fputs("Dateipfad fehlt.\n", stderr); exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let ext = url.pathExtension.lowercased()
var result = ""
if ext == "pdf" {
    guard let document = PDFDocument(url: url) else { fputs("PDF konnte nicht geöffnet werden.\n", stderr); exit(3) }
    for index in 0..<min(document.pageCount, 30) {
        guard let page = document.page(at: index) else { continue }
        let embedded = page.string ?? ""
        if embedded.trimmingCharacters(in: .whitespacesAndNewlines).count > 20 {
            result += embedded + "\n"
        } else {
            let thumb = page.thumbnail(of: NSSize(width: 2200, height: 3000), for: .mediaBox)
            if let data = thumb.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data), let image = bitmap.cgImage {
                result += ocr(image) + "\n"
            }
        }
    }
} else {
    guard let source = NSImage(contentsOf: url),
          let image = source.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        fputs("Bild konnte nicht geöffnet werden.\n", stderr); exit(4)
    }
    result = ocr(image)
}
print(result)
