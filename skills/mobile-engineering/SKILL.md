---
name: mobile-engineering
description: "Mobile implementation standards (Flutter, React Native, Expo, native Swift/Kotlin): navigation, permissions, secure storage, offline/network handling, platform conventions, accessibility, mobile testing without emulators, and approval rules for SDKs/emulators."
user-invocable: false
---

# Mobile engineering

## Activation criteria
Only when requirements include a mobile app (features.mobile). Mobile Engineer tasks; reviewers of mobile code.

## Required inputs
Task entry, API contract, DESIGN_SYSTEM.md (mobile layouts), target platforms/OS versions, chosen framework.

## Framework decision tree
- Team/web stack is TypeScript and app is standard CRUD/content → **Expo (React Native)** with Expo Router; bare React Native only for native modules Expo can't provide.
- Highly custom UI/animation or a Dart team → **Flutter** (Material 3/Cupertino, go_router, Riverpod/Bloc only if state is complex).
- Deep platform integration (widgets, background modes, ARKit) or a single platform → native **Swift/SwiftUI** or **Kotlin/Jetpack Compose**.

## Implementation guidance
- Navigation: typed routes, deep links validated (never trust parameters), back behaviour correct on Android.
- Permissions: request just-in-time with a rationale screen; handle "denied" and "denied permanently" (link to settings); declare usage strings (iOS `Info.plist`, Android manifest) only for used permissions.
- Secure storage: Keychain/Keystore via `expo-secure-store`, `flutter_secure_storage`, `EncryptedSharedPreferences`; never AsyncStorage/SharedPreferences for tokens.
- Network: timeouts, retries for idempotent requests, offline detection, optimistic UI only with reconciliation; certificate validation on; no cleartext HTTP in production.
- Layout: safe areas, dynamic type/font scaling, orientation, small/large devices; touch targets 44 pt (iOS) / 48 dp (Android).
- Accessibility: `accessibilityLabel`/`Semantics`, screen-reader order, contrast per design tokens.
- Config: API base URL and keys via build-time env (`app.config.ts` extra / `--dart-define`), never committed secrets.

## Testing
Unit tests for logic; component/widget tests (`@testing-library/react-native`, `flutter test`) that run without an emulator; integration/E2E (Detox, Maestro, `integration_test`) only when emulator/simulator use is approved — report them NOT_RUN otherwise.

## Approval boundaries
Installing Xcode/Android SDK, CocoaPods globally, starting emulators/simulators, or signing builds changes the user's system — report BLOCKED with exact commands instead of running them.

## Anti-patterns
Tokens in plain storage; blocking UI thread with heavy work; ignoring permission denial; hardcoded dimensions; platform checks scattered everywhere instead of adaptive components; shipping debug flags.

## Validation
`npx expo-doctor`/`tsc --noEmit`/`eslint` or `flutter analyze`; unit/component tests; record which checks need an emulator and were not run.

## Completion criteria
Screens and navigation per spec, permissions and storage secure, network failures handled, emulator-free tests passing, emulator-dependent checks explicitly reported.

## References
- Expo: https://docs.expo.dev/ · React Native: https://reactnative.dev/docs/getting-started
- Flutter: https://docs.flutter.dev/ · Apple HIG: https://developer.apple.com/design/human-interface-guidelines/
- Android developers: https://developer.android.com/guide · OWASP MASVS: https://mas.owasp.org/MASVS/

## Example (Expo secure token storage)
```ts
import * as SecureStore from 'expo-secure-store'

export async function saveSession(token: string) {
  await SecureStore.setItemAsync('session', token, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY })
}
export const loadSession = () => SecureStore.getItemAsync('session')   // null when signed out
export const clearSession = () => SecureStore.deleteItemAsync('session')
```
