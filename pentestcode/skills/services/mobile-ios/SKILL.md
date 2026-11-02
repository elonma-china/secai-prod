---
name: svc-mobile-ios
description: iOS IPA static analysis — hardcoded secrets, ATS bypass, Keychain, jailbreak detection, reverse engineering with class-dump/ipsw. Use when target is an IPA/iOS app. Triggers - IPA, iOS, Mach-O, class-dump, keychain, ATS, TestFlight.
---

# iOS IPA Static Analysis

Pair with `svc-mobile-android` concepts — same audit axes, Apple-specific mechanics.

## Reverse engineering
```
# Decrypt (FairPlay) if App Store binary: ipsw / clutch / frida-ios-dump
# Class/object dump
class-dump -H BinaryName                    # headers from Mach-O
Hopper / Ghidra for control flow
strings -a BinaryName | grep -iE 'key|token|secret|password|api'
otool -l BinaryName | grep -A4 LC_ENCRYPTION_INFO   # cryptid 1 = still encrypted
```

## Checklist (mirrors OWASP Mobile Top 10)
1. **Secrets**: hardcoded API keys in `Info.plist`, `*.strings`, binaries (strings + nm). Filter bundle IDs / public Firebase keys as non-issues — private keys and backend secrets count.
2. **ATS**: `NSAllowsArbitraryLoads=true` in Info.plist = cleartext allowed (High). Per-domain exceptions → note which domains.
3. **Keychain**: what's stored, with which accessibility (`kSecAttrAccessibleAlways` = leak on jailbreak). Data Protection class per file (NSFileProtectionComplete?).
4. **Local storage**: UserDefaults / SQLite / CoreData / Realm for tokens & PII — unencrypted = finding.
5. **Jailbreak detection**: file checks, scheme checks, fork() — document bypassability (Frida bypass = detection is bypassed, still note it as a control).
6. **Reverse-tamper**: only jailbreak checks, no integrity/signature verification of own binary?
7. **Exported schemes**: `CFBundleURLTypes` — custom URL handlers accepting unvalidated input (deep-link injection, auth bypass like `myapp://reset?token=`)
8. **Pasteboard / screenshots**: sensitive screens with `isSecureTextEntry` missing, screenshot in app switcher.
9. **Transport**: certificate pinning present? (check for AFSecurityPolicy/TrustKit/pinning libs — absence on a banking/health app = finding)
10. **Privacy manifests**: `NSUserTrackingUsageDescription`, excessive `NS*UsageDescription` vs actual functionality.

## Dynamic (when in scope)
Frida: `frida -U -f com.bundle.id -l script.js` — hook NSURLSession to log traffic, bypass pinning (`ssl-kill-switch2`), dump Keychain.
 objection: `objection explore` — root/jailbreak detection bypass, clipboard monitor, cookie dump.

## Report notes
- iOS findings often: ATS off, secrets in binary, weak jailbreak detection, unencrypted local data.
- Distinguish TestFlight/dev builds (debug features expected) from production App Store binaries.
