// Everything the iOS (Capacitor) shell does differently from the website
// lives here, so App.tsx stays platform-agnostic and every call degrades to
// a no-op / web behavior in the browser.

import { Capacitor } from '@capacitor/core'

export const isNative = Capacitor.isNativePlatform()

// One stable identifier: a new plan replaces the previous nudge instead of
// stacking notifications for trips that were re-planned five times.
const TRIP_NUDGE_ID = 33001

/**
 * Schedule the "how did that trip go?" nudge as a LOCAL notification - no
 * push infrastructure, no account, works fully offline. Fires a while after
 * the drive should have ended; opening the app then shows the existing
 * log-your-arrival prompt (storage.shouldPromptForPendingTrip), so the
 * notification needs no payload handling at all.
 */
export async function scheduleTripLogNudge(params: {
  originLabel: string
  destinationLabel: string
  departureEpoch: number | null // seconds; null = leaving now
  durationMin: number
}) {
  if (!isNative) return
  try {
    const { LocalNotifications } = await import('@capacitor/local-notifications')
    const perm = await LocalNotifications.requestPermissions()
    if (perm.display !== 'granted') return

    const departMs = params.departureEpoch ? params.departureEpoch * 1000 : Date.now()
    // Trip end plus a 90-minute buffer for the stops real drives include.
    let fireAt = departMs + params.durationMin * 60_000 + 90 * 60_000
    // Never fire in the small hours - a 11pm arrival gets asked at 9am.
    const at = new Date(fireAt)
    if (at.getHours() >= 22 || at.getHours() < 9) {
      at.setHours(9, 0, 0, 0)
      if (at.getTime() < fireAt) at.setDate(at.getDate() + 1)
      fireAt = at.getTime()
    }

    await LocalNotifications.cancel({ notifications: [{ id: TRIP_NUDGE_ID }] })
    await LocalNotifications.schedule({
      notifications: [
        {
          id: TRIP_NUDGE_ID,
          title: 'How did that trip go?',
          body:
            `${params.originLabel.split(',')[0]} → ${params.destinationLabel.split(',')[0]} - ` +
            'log what the battery actually showed. Ten seconds, and Leeway learns your car.',
          schedule: { at: new Date(fireAt) },
        },
      ],
    })
  } catch {
    // Notifications are a nice-to-have; a plan must never fail over them.
  }
}

export async function cancelTripLogNudge() {
  if (!isNative) return
  try {
    const { LocalNotifications } = await import('@capacitor/local-notifications')
    await LocalNotifications.cancel({ notifications: [{ id: TRIP_NUDGE_ID }] })
  } catch {
    // ignore
  }
}

/**
 * Share text natively when possible. WKWebView has no navigator.share, so
 * the web path's Web Share API silently doesn't exist inside the app -
 * this routes through the Capacitor plugin there and falls back to the
 * caller's own web logic elsewhere (return false = caller handles it).
 */
export async function shareNative(payload: { title?: string; text?: string; url?: string }): Promise<boolean> {
  if (!isNative) return false
  try {
    const { Share } = await import('@capacitor/share')
    await Share.share(payload)
    return true
  } catch {
    // Cancelled or unavailable - treat cancel as handled so the caller
    // doesn't ALSO open a clipboard fallback on an intentional dismiss.
    return true
  }
}

/** Match the native status bar to the app theme. No-op on the web. */
export async function syncStatusBar(theme: 'light' | 'dark') {
  if (!isNative) return
  try {
    const { StatusBar, Style } = await import('@capacitor/status-bar')
    await StatusBar.setStyle({ style: theme === 'dark' ? Style.Dark : Style.Light })
  } catch {
    // ignore
  }
}
