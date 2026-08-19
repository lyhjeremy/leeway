# Leeway iOS: build & ship handoff

Everything on the code side is done. This doc is for the person with the
Apple Developer account: you need a Mac with **Xcode 15 or newer** and
nothing else, no Node, no CocoaPods (the project uses Swift Package
Manager, and the built web bundle is committed).

## What this app is

The Leeway web app (React, in `frontend/`) wrapped in a Capacitor native
shell, plus three native features the website can't do:

- **Trip-log reminder**: after you plan a trip, a local notification fires
  once the drive should be over ("How did that trip go?"). Opening the app
  shows the log prompt. Scheduled on-device; there is no push server.
- **Native share sheet**: sharing a charging stop hands a maps link to the
  share sheet (that's how a stop reaches the Tesla app).
- **Status bar / safe areas** matching the app's light and dark themes.

The app talks to the same production backend as the website
(`https://leeway-api.onrender.com`). Its CORS already allows the app's
origin. No server changes needed.

## Build steps

1. Clone the repo and open `frontend/ios/App/App.xcodeproj` in Xcode.
   First open takes a few minutes while Xcode resolves Swift packages
   (Capacitor core comes from GitHub; the three plugins are vendored in
   `frontend/ios/vendor/`, so no npm install is ever needed). Internet is
   required for that first resolution.
2. In the App target → Signing & Capabilities: select your team. If the
   bundle ID `io.github.lyhjeremy.leeway` can't be registered under your
   account, change it, and mirror the change in
   `frontend/capacitor.config.ts` (`appId`) so future regenerations match.
3. Add the **Push Notifications capability? No.** Not needed; the reminder
   is a local notification, no capability or APNs key required.
4. Run on a simulator first, then a device. Archive → Distribute →
   App Store Connect for TestFlight.

## What to smoke-test on a device

- Plan the sample trip (Culver City → Mission District). Verify the map,
  verdict card, and charging stops render.
- Accept the notification permission prompt after planning; the reminder is
  scheduled for trip end + 90 min (daytime-clamped). For a quick test, set
  a departure time ~now; you can also just verify the permission flow.
- Share a charging stop (share link next to a stop) → native sheet opens.
- Locate-me chip → location permission prompt appears (usage string is set).
- Toggle dark mode → status bar text flips with it.
- Kill and reopen the app after planning: the "how did that trip go?"
  prompt should appear on the reopen (this is the core Stage 5 loop).

## App Store submission notes

- Privacy policy URL: `https://lyhjeremy.github.io/leeway/privacy.html`
  (already live, honest, and matches what the app does).
- App Privacy questionnaire: no data collected, no tracking. Location is
  used but not stored or linked to identity. `ITSAppUsesNonExemptEncryption`
  is already set to false, so no export-compliance question.
- Suggested listing: category Navigation; subtitle "The second opinion
  before you leave"; the app plans EV road trips against your car's REAL
  degraded range, with verified charging stops and safety flags.
- Known quotas: routing runs on a free tier good for roughly 150–200
  plans/day total across all users. Fine for TestFlight; if the app gets
  real distribution, the routing backend needs the planned self-hosted
  upgrade first (documented in the product plan).

## Known limitations (by design, not bugs)

- Voice search's microphone is hidden in the app (WKWebView has no speech
  recognition); typing in the same bar works. The mic works on the website.
- The map tiles come from OpenFreeMap's free CDN, which can briefly 503
  under load, panels stay usable and tiles fill in.

## If the web bundle ever needs rebuilding

Only needed when frontend code changes (requires Node 20+):

```
cd frontend && npm ci && npm run sync:ios
```

That rebuilds the web app with app-relative paths, copies it into
`ios/App/App/public/`, and re-vendors the Swift plugin packages (cap sync
resets their paths to node_modules; the script puts them back). All of it
is committed, so you normally never run any of this.
