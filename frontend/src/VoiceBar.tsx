import { useRef, useState } from 'react'
import { voiceSearch } from './api'
import type { GeocodeResult, Units, VoiceSearchResponse } from './types'

const MI_TO_KM = 1.609344

interface Props {
  origin: GeocodeResult
  destination: GeocodeResult
  units: Units
  onAddStop: (lat: number, lon: number, title: string) => void
}

// Voice, not typing, is the point here per the product plan: this bar lives
// on a phone mounted in a car (Stage 4 comes after Stage 3's mobile work on
// purpose), where speaking is genuinely faster than typing - at a laptop,
// typing would win, which is why this wasn't built earlier.
export default function VoiceBar({ origin, destination, units, onAddStop }: Props) {
  const dist = (mi: number) => {
    const v = units === 'km' ? mi * MI_TO_KM : mi
    return `${v < 10 ? Math.round(v * 10) / 10 : Math.round(v)} ${units}`
  }
  const [text, setText] = useState('')
  const [listening, setListening] = useState(false)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<VoiceSearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const recognitionRef = useRef<any>(null)

  const SpeechRecognitionCtor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  const speechSupported = !!SpeechRecognitionCtor

  function startListening() {
    if (!SpeechRecognitionCtor) return
    const recognition = new SpeechRecognitionCtor()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.maxAlternatives = 1
    recognition.onresult = (e: any) => {
      const transcript = e.results[0][0].transcript
      setText(transcript)
      runSearch(transcript)
    }
    recognition.onerror = () => setListening(false)
    recognition.onend = () => setListening(false)
    recognitionRef.current = recognition
    setListening(true)
    recognition.start()
  }

  async function runSearch(query: string) {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await voiceSearch(query, { lat: origin.lat, lon: origin.lon }, { lat: destination.lat, lon: destination.lon })
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="voice-bar-wrap">
      <div className="voice-bar">
        <svg width="14" height="14" viewBox="0 0 24 24" className="voice-search-icon">
          <circle cx="11" cy="11" r="7" fill="none" stroke="#52514e" strokeWidth="1.8" />
          <line x1="16.5" y1="16.5" x2="21" y2="21" stroke="#52514e" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <input
          value={text}
          placeholder="Add a stop along the route…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') runSearch(text)
          }}
        />
        {speechSupported && (
          <button
            className={`mic-btn ${listening ? 'listening' : ''}`}
            onClick={startListening}
            disabled={loading}
            title="Speak your request"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="#F5F5F0">
              <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" />
            </svg>
          </button>
        )}
      </div>

      {loading && <div className="voice-chip">Searching…</div>}
      {error && <div className="voice-chip voice-chip-error">{error}</div>}
      {result && (
        <div className="voice-chip">
          <div className="voice-chip-head">
            <div className="voice-query">“{text}”</div>
            <button className="voice-close" onClick={() => setResult(null)} aria-label="Hide results">
              </button>
          </div>
          {result.note && <div className="voice-note">{result.note}</div>}
          {result.results.length === 0 && !result.note && <div className="voice-note">No matches found near the route.</div>}
          {result.results.map((r, i) => (
            <div className="voice-result-row" key={i}>
              <div>
                <div className="voice-result-name">{r.name}</div>
                {r.address && <div className="voice-result-sub">{r.address}</div>}
                <div className="voice-result-sub">
                  {r.drive_through === 'yes' ? 'Drive-through · ' : ''}
                  adds {r.detour_min >= 0 ? `${r.detour_min} min` : `${r.detour_min} min`}
                  {r.detour_mi != null && ` · ${dist(r.detour_mi)}`}
                  {!r.within_budget && ' (over your ask)'}
                </div>
              </div>
              <button
                className="link-btn"
                onClick={() => {
                  // Middle dot, not a comma: waypoint titles get trimmed at
                  // the first comma for display, which would strip the
                  // address right back off.
                  onAddStop(r.lat, r.lon, r.address ? `${r.name} · ${r.address.split(',')[0]}` : r.name)
                  // The stop is chosen - clear the results so the map (and
                  // the replanned route) is visible again.
                  setResult(null)
                }}
              >
                add
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
