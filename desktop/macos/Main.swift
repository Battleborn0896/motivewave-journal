import AppKit
import Foundation
import ImageIO
import WebKit

let appName = "MotiveWave Journal"

struct Configuration: Decodable {
    let python: String?
    let executable: String?
    let project: String
    let port: Int
}

/// IBM Plex Sans for the startup page, read from the bundled journal (journal/app/static/fonts) and
/// written into the page, which is shown before the local backend that serves the fonts is running.
func startupFontFaces() -> String {
    guard let fonts = Bundle.main.resourceURL?.appendingPathComponent("journal/app/static/fonts") else { return "" }
    return [("IBMPlexSans-Regular.woff2", 400), ("IBMPlexSans-Bold.woff2", 700)].compactMap { file, weight in
        guard let data = try? Data(contentsOf: fonts.appendingPathComponent(file)) else { return nil }
        return "@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:\(weight);"
            + "src:url(data:font/woff2;base64,\(data.base64EncodedString())) format('woff2')}"
    }.joined()
}

func makeIcon(_ directory: String) throws {
    let folder = URL(fileURLWithPath: directory, isDirectory: true)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    // Render offscreen with CoreGraphics, without starting NSApplication or contacting WindowServer.
    for size in [16, 32, 128, 256, 512] {
        for scale in [1, 2] {
            let pixels = size * scale
            let context = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8,
                bytesPerRow: pixels * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
            func rectangle(_ rect: CGRect, radius: CGFloat) {
                context.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
                context.fillPath()
            }
            context.setFillColor(CGColor(red: 0.082, green: 0.086, blue: 0.090, alpha: 1))
            rectangle(CGRect(x: 55, y: 55, width: 914, height: 914), radius: 210)
            context.setFillColor(CGColor(red: 0.886, green: 0.812, blue: 0.196, alpha: 1))
            for (x, low, high) in [(260.0, 250.0, 520.0), (440.0, 370.0, 680.0), (620.0, 500.0, 800.0)] {
                rectangle(CGRect(x: x, y: low, width: 98, height: high - low), radius: 16)
                rectangle(CGRect(x: x + 44, y: low - 66, width: 10, height: high - low + 132), radius: 5)
            }
            let suffix = scale == 2 ? "@2x" : ""
            let url = folder.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
            guard let image = context.makeImage(),
                  let output = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
                throw NSError(domain: appName, code: 10)
            }
            // iconutil on current macOS rejects otherwise-valid RGBA PNGs whose EXIF metadata omits
            // an explicit sRGB colour-space tag. Keep the generated icon self-contained and portable.
            let properties = [kCGImagePropertyExifDictionary: [kCGImagePropertyExifColorSpace: 1]] as CFDictionary
            CGImageDestinationAddImage(output, image, properties)
            guard CGImageDestinationFinalize(output) else { throw NSError(domain: appName, code: 11) }
        }
    }
}

final class JournalApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var web: WKWebView!
    private var backend: Process?
    private var journalURL: URL?
    private var logURL: URL!
    private var starting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.appearance = NSAppearance(named: .darkAqua)
        menu()
        let frame = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 960)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: min(1440, frame.width - 40),
            height: min(960, frame.height - 40)), styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = appName
        window.subtitle = "Local desktop journal"
        window.minSize = NSSize(width: 960, height: 680)
        window.setFrameAutosaveName("MotiveWaveJournalWindow")
        window.isReleasedWhenClosed = false
        window.backgroundColor = NSColor(calibratedWhite: 0.082, alpha: 1)
        web = WKWebView(frame: .zero)
        web.navigationDelegate = self
        web.uiDelegate = self
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        start()
    }

    private func menu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu(title: appName)
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        editItem.submenu = edit
        for (title, selector, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                                      ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                      ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: "View")
        viewItem.submenu = view
        view.addItem(withTitle: "Reload Journal", action: #selector(reload), keyEquivalent: "r")
        view.addItem(withTitle: "Zoom In", action: #selector(zoomIn), keyEquivalent: "+")
        view.addItem(withTitle: "Zoom Out", action: #selector(zoomOut), keyEquivalent: "-")
        view.addItem(withTitle: "Actual Size", action: #selector(actualSize), keyEquivalent: "0")
        let helpItem = NSMenuItem()
        main.addItem(helpItem)
        let help = NSMenu(title: "Help")
        helpItem.submenu = help
        help.addItem(withTitle: "Show Journal Data Folder", action: #selector(showData), keyEquivalent: "")
        help.addItem(withTitle: "Show Desktop Log", action: #selector(showLog), keyEquivalent: "")
        NSApp.mainMenu = main
    }

    @objc private func about() {
        NSApp.orderFrontStandardAboutPanel(options: [NSApplication.AboutPanelOptionKey.applicationName: appName,
            .applicationVersion: "0.4.18", .credits: NSAttributedString(string:
                "Local-first MotiveWave journal. Native macOS window with an embedded journal interface.\n"
                + "Distribution builds include their own local journal backend.\n"
                + "Trades are recorded by the read-only MotiveWave study; the app has no screen recorder.")])
    }
    @objc private func reload() { if journalURL == nil { start() } else { web.reload() } }
    @objc private func zoomIn() { web.pageZoom = min(2, web.pageZoom + 0.1) }
    @objc private func zoomOut() { web.pageZoom = max(0.6, web.pageZoom - 0.1) }
    @objc private func actualSize() { web.pageZoom = 1 }
    private var dataFolder: URL {
        if let path = ProcessInfo.processInfo.environment["MWJ_HOME"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/MotiveWave Journal", isDirectory: true)
    }
    @objc private func showData() { NSWorkspace.shared.open(dataFolder) }
    @objc private func showLog() { if let url = logURL { NSWorkspace.shared.activateFileViewerSelecting([url]) } }

    private func start() {
        if starting { return }
        starting = true
        web.loadHTMLString("""
            <html><head><style>\(startupFontFaces())</style></head>
            <body style="background:#151617;color:#E8E8E5;font:16px 'IBM Plex Sans',-apple-system,sans-serif;padding:40px">
            <h2>MotiveWave Journal</h2><p>Starting your local journal…</p>
            <p>Waiting for the local study-data reader.</p></body></html>
            """, baseURL: nil)
        do {
            guard let resources = Bundle.main.resourceURL else { throw NSError(domain: appName, code: 1) }
            let config = try JSONDecoder().decode(Configuration.self,
                from: Data(contentsOf: resources.appendingPathComponent("launcher.json")))
            let source = resources.appendingPathComponent(config.project, isDirectory: true)
            logURL = dataFolder.appendingPathComponent("desktop/backend.log")
            let process = Process()
            if let configured = config.executable, !configured.isEmpty {
                let executable = configured.hasPrefix("/") ? URL(fileURLWithPath: configured)
                    : resources.appendingPathComponent(configured)
                process.executableURL = executable
                process.arguments = ["--project", source.path, "--port", String(config.port), "--log", logURL.path]
            } else if let python = config.python, !python.isEmpty {
                process.executableURL = URL(fileURLWithPath: python)
                process.arguments = ["-m", "mwjournal.desktop", "--project", source.path,
                                     "--port", String(config.port), "--log", logURL.path]
            } else {
                throw NSError(domain: appName, code: 5,
                    userInfo: [NSLocalizedDescriptionKey: "The bundled journal backend is missing."])
            }
            process.currentDirectoryURL = source
            process.environment = ProcessInfo.processInfo.environment.merging([
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": source.path]) { _, new in new }
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            backend = process
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                let data = output.fileHandleForReading.availableData
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.starting = false
                    do {
                        guard let response = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                            throw NSError(domain: appName, code: 2,
                                userInfo: [NSLocalizedDescriptionKey: "The Python backend could not start. Rebuild with a working Python environment."])
                        }
                        if let error = response["error"] as? String {
                            throw NSError(domain: appName, code: 3, userInfo: [NSLocalizedDescriptionKey: error])
                        }
                        guard let value = response["url"] as? String, let url = URL(string: value),
                              url.scheme == "http", url.host == "127.0.0.1" else {
                            throw NSError(domain: appName, code: 4)
                        }
                        self.journalURL = url
                        self.web.load(URLRequest(url: url))
                    } catch { self.failure(error.localizedDescription) }
                }
            }
        } catch {
            starting = false
            failure(error.localizedDescription)
        }
    }

    private func failure(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "The local journal could not open"
        alert.informativeText = message + "\n\nView → Reload Journal retries. Help → Show Desktop Log opens diagnostics."
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    private func isLocal(_ url: URL) -> Bool {
        guard let origin = journalURL else { return url.scheme == "about" }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.targetFrame?.isMainFrame == false || isLocal(url) || url.scheme == "about" {
            decisionHandler(.allow)
        } else {
            if action.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isLocal(url) { web.load(URLRequest(url: url)) }
            else if ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        }
        return nil
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        // The controller only terminates a backend it started. Existing browser sessions are untouched.
        if let process = backend, process.isRunning { process.terminate() }
    }
}

if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--make-icon" {
    do { try makeIcon(CommandLine.arguments[2]); exit(0) }
    catch { fputs("Icon generation failed: \(error)\n", stderr); exit(1) }
}
let application = NSApplication.shared
let delegate = JournalApp()
application.delegate = delegate
application.run()
