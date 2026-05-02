import UIKit
import WebKit

class ViewController: UIViewController {

    private var webView: WKWebView!
    private let appURL = URL(string: "https://web-production-2c0f89.up.railway.app/app")!

    override func loadView() {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.scrollView.bounces = false
        view = webView
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        webView.load(URLRequest(url: appURL))
    }

    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
}

extension ViewController: WKUIDelegate {

    // Automatically grant camera permission for barcode scanning
    @available(iOS 15.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.grant)
    }

    // Forward JS alert() calls to native alerts
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        present(alert, animated: true)
    }
}

extension ViewController: WKNavigationDelegate {

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let alert = UIAlertController(
            title: "Connection Error",
            message: "Could not reach Inkling. Check your internet connection and try again.",
            preferredStyle: .alert
        )
        alert.addAction(UIAlertAction(title: "Retry", style: .default) { [weak self] _ in
            guard let self else { return }
            self.webView.load(URLRequest(url: self.appURL))
        })
        present(alert, animated: true)
    }
}
