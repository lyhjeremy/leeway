import type { CapacitorConfig } from '@capacitor/cli'

// The iOS app is the same web app in a native shell - see src/native.ts for
// the few places the app behaves differently (trip-log notification, share
// sheet, no service worker). appId must match whatever bundle ID the Apple
// developer account registers; change it here AND in Xcode signing settings
// together if it needs to differ.
const config: CapacitorConfig = {
  appId: 'io.github.lyhjeremy.leeway',
  appName: 'Leeway',
  webDir: 'dist',
  ios: {
    // The web layout already handles its own scrolling; never let the
    // webview add automatic content insets on top of that.
    contentInset: 'never',
  },
}

export default config
