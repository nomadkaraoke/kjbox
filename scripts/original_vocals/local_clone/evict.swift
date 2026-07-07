// Evict (dehydrate) a materialized File Provider item back to online-only to
// reclaim local disk. Usage: evict <path>  (exit 0 = evicted)
//
// NOTE / LIMITATION: this only works when the file belongs to a real macOS
// *File Provider* domain. It does NOT work for Dropbox's *legacy Smart Sync*
// engine (which is what shipped on the machine this was built for): there is no
// File Provider domain (NSFileProviderManager.domains() is empty), so
// getIdentifierForUserVisibleFile returns "file doesn't exist" and nothing can
// be evicted programmatically. On such setups, reclaim space by selecting the
// folder in Finder -> right-click -> Dropbox -> "Make Online-Only". Kept here for
// setups that DO use the modern File Provider Dropbox, and as documentation of
// the approach that was investigated.
import Foundation
import FileProvider

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write("usage: evict <path>\n".data(using: .utf8)!); exit(2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let sem = DispatchSemaphore(value: 0)
var rc: Int32 = 0

Task {
    // Enumerate domains first — File Provider only begins tracking user-visible
    // files after getDomains has been called at least once this process.
    let domains = (try? await NSFileProviderManager.domains()) ?? []
    let resolved = url.resolvingSymlinksInPath()
    NSFileProviderManager.getIdentifierForUserVisibleFile(at: resolved) { itemId, domainId, err in
        guard let itemId = itemId, let domainId = domainId else {
            FileHandle.standardError.write("no provider item: \(String(describing: err))\n".data(using: .utf8)!)
            rc = 1; sem.signal(); return
        }
        guard let domain = domains.first(where: { $0.identifier == domainId }),
              let mgr = NSFileProviderManager(for: domain) else {
            FileHandle.standardError.write("no manager for domain \(domainId.rawValue)\n".data(using: .utf8)!)
            rc = 1; sem.signal(); return
        }
        mgr.evictItem(identifier: itemId) { evErr in
            if let evErr = evErr {
                FileHandle.standardError.write("evict error: \(evErr.localizedDescription)\n".data(using: .utf8)!)
                rc = 1
            }
            sem.signal()
        }
    }
}
sem.wait()
exit(rc)
