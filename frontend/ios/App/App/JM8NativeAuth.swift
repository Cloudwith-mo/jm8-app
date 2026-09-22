import UIKit
import Capacitor
import AuthenticationServices
import Security

// Development prototype only: never accept arbitrary authentication hosts.
final class JM8BridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(JM8NativeAuth())
    }

}

@objc(JM8NativeAuth)
public class JM8NativeAuth: CAPPlugin, CAPBridgedPlugin, ASWebAuthenticationPresentationContextProviding {
    public let identifier = "JM8NativeAuth"
    public let jsName = "JM8NativeAuth"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "authenticate", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getSession", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setSession", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearSession", returnType: CAPPluginReturnPromise)
    ]
    private var session: ASWebAuthenticationSession?
    private var presentationWindow: UIWindow?
    private let callbackScheme = "com.cloudwithmo.journalm8.dev"
    private let domain = "journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com"

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        return presentationWindow ?? ASPresentationAnchor()
    }

    @objc func authenticate(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard self.session == nil else {
                call.reject("An authentication session is already open.", "AUTH_BUSY")
                return
            }
            guard Bundle.main.bundleIdentifier == self.callbackScheme,
                  let text = call.getString("url"), let url = URL(string: text),
                  url.scheme == "https", url.host == self.domain,
                  url.port == nil, url.user == nil, url.password == nil,
                  ["/oauth2/authorize", "/signup", "/logout"].contains(url.path),
                  let callback = call.getString("callback"),
                  ["\(self.callbackScheme)://auth/callback", "\(self.callbackScheme)://auth/logout"].contains(callback),
                  let window = self.bridge?.viewController?.view.window else {
                call.reject("Invalid development authentication configuration.", "AUTH_CONFIG")
                return
            }
            self.presentationWindow = window
            let auth = ASWebAuthenticationSession(url: url, callbackURLScheme: self.callbackScheme) { result, error in
                DispatchQueue.main.async {
                    self.session = nil
                    self.presentationWindow = nil
                    if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                        call.reject("Sign-in was canceled.", "AUTH_CANCELLED")
                        return
                    }
                    guard error == nil, let result = result,
                          var parts = URLComponents(url: result, resolvingAgainstBaseURL: false) else {
                        call.reject("Authentication could not be completed.", "AUTH_FAILED")
                        return
                    }
                    parts.query = nil
                    parts.fragment = nil
                    guard parts.string == callback else {
                        call.reject("Unexpected authentication callback.", "AUTH_CALLBACK")
                        return
                    }
                    call.resolve(["url": result.absoluteString])
                }
            }
            auth.presentationContextProvider = self
            // Keep each sign-in explicit; no persistent browser SSO cookie to clear.
            auth.prefersEphemeralWebBrowserSession = true
            self.session = auth
            if !auth.start() {
                self.session = nil
                self.presentationWindow = nil
                call.reject("Could not open sign-in.", "AUTH_START")
            }
        }
    }

    private var key: [String: Any] {
        return [kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: "com.cloudwithmo.journalm8.dev.session",
                kSecAttrAccount as String: "cognito"]
    }

    @objc func getSession(_ call: CAPPluginCall) {
        var query = key
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &value)
        if status == errSecItemNotFound { call.resolve(); return }
        guard status == errSecSuccess, let data = value as? Data,
              let text = String(data: data, encoding: .utf8) else {
            call.reject("Could not read the secure session.", "KEYCHAIN_READ")
            return
        }
        call.resolve(["value": text])
    }

    @objc func setSession(_ call: CAPPluginCall) {
        guard let value = call.getString("value"), value.utf8.count <= 65536,
              let data = value.data(using: .utf8) else {
            call.reject("Invalid session.", "KEYCHAIN_VALUE"); return
        }
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = key
            attributes.forEach { item[$0.key] = $0.value }
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            call.reject("Could not save the secure session.", "KEYCHAIN_WRITE"); return
        }
        call.resolve()
    }

    @objc func clearSession(_ call: CAPPluginCall) {
        let status = SecItemDelete(key as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            call.reject("Could not clear the secure session.", "KEYCHAIN_DELETE"); return
        }
        call.resolve()
    }
}
