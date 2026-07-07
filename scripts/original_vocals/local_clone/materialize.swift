// Force-materialize an online-only Dropbox (File Provider) placeholder by doing
// an NSFileCoordinator coordinated read with the .forUploading hint, which makes
// the provider fault in the FULL file and blocks until it is available locally.
// Raw read()/cat does NOT trigger this — only a coordinated read does.
//
// Usage: materialize <path>   (exit 0 = materialized, non-zero = error)
import Foundation

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write("usage: materialize <path>\n".data(using: .utf8)!)
    exit(2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let coordinator = NSFileCoordinator()
var coordErr: NSError?
var readOK = false
coordinator.coordinate(readingItemAt: url, options: [.forUploading], error: &coordErr) { u in
    if let fh = try? FileHandle(forReadingFrom: u) {
        _ = try? fh.read(upToCount: 1)   // faulting already happened via coordination
        try? fh.close()
        readOK = true
    }
}
if let e = coordErr {
    FileHandle.standardError.write("coordination error: \(e.localizedDescription)\n".data(using: .utf8)!)
    exit(1)
}
exit(readOK ? 0 : 1)
