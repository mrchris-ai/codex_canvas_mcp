#!/usr/bin/env swift

import Darwin
import Dispatch
import Foundation
import Security


enum HelperError: Error, CustomStringConvertible {
    case message(String)

    var description: String {
        switch self {
        case .message(let text):
            return text
        }
    }
}


struct Configuration {
    let service: String
    let account: String
    let vault: String
    let item: String
    let opPath: String

    static func parse(_ arguments: [String]) throws -> Configuration {
        var values: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            if argument == "--help" || argument == "-h" {
                printUsage()
                exit(0)
            }
            guard ["--service", "--account", "--vault", "--item", "--op-path"].contains(argument) else {
                throw HelperError.message("Unknown argument: \(argument)")
            }
            index += 1
            guard index < arguments.count, !arguments[index].isEmpty else {
                throw HelperError.message("Missing value for \(argument)")
            }
            values[argument] = arguments[index]
            index += 1
        }

        func required(_ name: String) throws -> String {
            guard let value = values[name] else {
                throw HelperError.message("Missing required argument: \(name)")
            }
            return value
        }

        let opPath = try values["--op-path"] ?? findExecutable("op")
        return Configuration(
            service: try required("--service"),
            account: try required("--account"),
            vault: try required("--vault"),
            item: try required("--item"),
            opPath: opPath
        )
    }
}


struct KeychainItem {
    let persistentReference: Data
    let value: Data
}


func printUsage() {
    print(
        """
        Usage:
          swift scripts/store_service_token_macos.swift \
            --service SERVICE \
            --account ACCOUNT \
            --vault VAULT \
            --item ITEM \
            [--op-path /absolute/path/to/op]

        Reads a 1Password service-account token from a hidden TTY prompt, validates
        access to the requested vault item, updates an existing macOS Keychain item,
        and verifies the exact stored value. It never accepts a token as an argument.
        """
    )
}


func findExecutable(_ name: String) throws -> String {
    let environment = ProcessInfo.processInfo.environment
    for directory in (environment["PATH"] ?? "").split(separator: ":") {
        let candidate = URL(fileURLWithPath: String(directory))
            .appendingPathComponent(name).path
        if access(candidate, X_OK) == 0 {
            return candidate
        }
    }
    throw HelperError.message("Could not find \(name) in PATH; pass --op-path.")
}


func readHiddenLine(prompt: String) throws -> String {
    guard isatty(STDIN_FILENO) == 1 else {
        throw HelperError.message("Refusing to read a service token without an interactive TTY.")
    }

    var original = termios()
    guard tcgetattr(STDIN_FILENO, &original) == 0 else {
        throw HelperError.message("Could not read terminal settings.")
    }
    var hidden = original
    hidden.c_lflag &= ~tcflag_t(ECHO)

    fputs(prompt, stdout)
    fflush(stdout)
    guard tcsetattr(STDIN_FILENO, TCSAFLUSH, &hidden) == 0 else {
        throw HelperError.message("Could not disable terminal echo.")
    }
    let value = readLine(strippingNewline: true)
    guard tcsetattr(STDIN_FILENO, TCSAFLUSH, &original) == 0 else {
        throw HelperError.message("Could not restore terminal echo.")
    }
    fputs("\n", stdout)

    guard let value, !value.isEmpty else {
        throw HelperError.message("No service-account token was entered.")
    }
    return value
}


func runOnePasswordValidation(token: String, configuration: Configuration) throws {
    guard token.hasPrefix("ops_") else {
        throw HelperError.message(
            "The value is not a 1Password service-account token."
        )
    }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: configuration.opPath)
    process.arguments = [
        "item", "get", configuration.item,
        "--vault", configuration.vault,
        "--format", "json",
    ]
    var environment = ProcessInfo.processInfo.environment
    environment["OP_SERVICE_ACCOUNT_TOKEN"] = token
    process.environment = environment
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice

    let completed = DispatchSemaphore(value: 0)
    process.terminationHandler = { _ in completed.signal() }
    try process.run()
    if completed.wait(timeout: .now() + 20) == .timedOut {
        process.terminate()
        if completed.wait(timeout: .now() + 1) == .timedOut {
            kill(process.processIdentifier, SIGKILL)
            _ = completed.wait(timeout: .now() + 1)
        }
        process.waitUntilExit()
        throw HelperError.message("1Password validation timed out.")
    }
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        throw HelperError.message("1Password validation failed.")
    }
}


func resolveExistingKeychainItem(configuration: Configuration) throws -> KeychainItem {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: configuration.service,
        kSecAttrAccount as String: configuration.account,
        kSecMatchLimit as String: kSecMatchLimitAll,
        kSecReturnPersistentRef as String: true,
    ]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound {
        throw HelperError.message(
            "The target Keychain item does not exist; refusing to create one without its reviewed ACL."
        )
    }
    guard status == errSecSuccess else {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
        throw HelperError.message("Keychain lookup failed: \(message)")
    }

    let references: [Data]
    if let matches = result as? [Data] {
        references = matches
    } else if let match = result as? Data {
        references = [match]
    } else {
        throw HelperError.message("Keychain lookup returned an unexpected result.")
    }
    guard references.count == 1, let reference = references.first else {
        throw HelperError.message(
            "Keychain lookup found \(references.count) matching items; refusing an ambiguous update."
        )
    }
    return KeychainItem(
        persistentReference: reference,
        value: try readKeychainValue(persistentReference: reference)
    )
}


func readKeychainValue(persistentReference: Data) throws -> Data {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecMatchItemList as String: [persistentReference],
        kSecMatchLimit as String: kSecMatchLimitOne,
        kSecReturnData as String: true,
    ]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound {
        throw HelperError.message("The target Keychain item disappeared during the update.")
    }
    guard status == errSecSuccess else {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
        throw HelperError.message("Keychain read failed: \(message)")
    }
    guard let value = result as? Data else {
        throw HelperError.message("Keychain read returned an unexpected result.")
    }
    return value
}


func updateKeychainValue(persistentReference: Data, value: Data) throws {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecMatchItemList as String: [persistentReference],
    ]
    let attributes: [String: Any] = [
        kSecValueData as String: value,
    ]
    let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
    guard status == errSecSuccess else {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
        throw HelperError.message("Keychain update failed: \(message)")
    }
}


func verifyKeychainValue(persistentReference: Data, expected: Data) throws {
    guard try readKeychainValue(persistentReference: persistentReference) == expected else {
        throw HelperError.message("Keychain verification found a value mismatch.")
    }
}


func replaceKeychainValue(token: String, configuration: Configuration) throws {
    let target = try resolveExistingKeychainItem(configuration: configuration)
    let replacement = Data(token.utf8)
    try updateKeychainValue(persistentReference: target.persistentReference, value: replacement)
    do {
        try verifyKeychainValue(
            persistentReference: target.persistentReference,
            expected: replacement
        )
    } catch {
        do {
            try updateKeychainValue(
                persistentReference: target.persistentReference,
                value: target.value
            )
            try verifyKeychainValue(
                persistentReference: target.persistentReference,
                expected: target.value
            )
        } catch {
            throw HelperError.message(
                "Keychain verification and rollback both failed; the stored value is uncertain."
            )
        }
        throw HelperError.message("Keychain verification failed; the original value was restored.")
    }
}


do {
    let configuration = try Configuration.parse(Array(CommandLine.arguments.dropFirst()))
    let token = try readHiddenLine(prompt: "Full 1Password service-account token: ")
    try runOnePasswordValidation(token: token, configuration: configuration)
    try replaceKeychainValue(token: token, configuration: configuration)
    print("Service-account token validated and stored successfully.")
    print("Stored token length: \(token.utf8.count)")
} catch {
    fputs("Error: \(error)\n", stderr)
    exit(1)
}
