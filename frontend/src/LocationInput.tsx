import { useEffect, useRef, useState } from 'react'
import { geocode } from './api'
import type { GeocodeResult } from './types'

interface Props {
  placeholder: string
  dotClass: string
  value: GeocodeResult | null
  onChange: (value: GeocodeResult | null) => void
}

export default function LocationInput({ placeholder, dotClass, value, onChange }: Props) {
  const [text, setText] = useState(value?.label ?? '')
  const [results, setResults] = useState<GeocodeResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [highlight, setHighlight] = useState(-1)
  const [open, setOpen] = useState(false)
  const debounceRef = useRef<number | undefined>(undefined)
  const latestQueryRef = useRef('')

  useEffect(() => {
    // Only sync on a real selection. Typing clears `value` (onChange(null)),
    // and syncing on that wiped the field mid-keystroke - typing "Foster
    // City" into a filled field came out as "oster City".
    if (value) setText(value.label)
  }, [value])

  async function search(query: string) {
    latestQueryRef.current = query
    setSearching(true)
    setOpen(true)
    try {
      const r = await geocode(query)
      // A slow older request must not overwrite a newer query's results
      if (latestQueryRef.current !== query) return
      setResults(r)
      setSearched(true)
      setHighlight(r.length > 0 ? 0 : -1)
    } catch {
      if (latestQueryRef.current === query) setResults([])
    } finally {
      if (latestQueryRef.current === query) setSearching(false)
    }
  }

  function handleInput(next: string) {
    setText(next)
    onChange(null)
    window.clearTimeout(debounceRef.current)
    // Old results are for the old text - keeping them visible while a new
    // search loads made the dropdown feel unresponsive to typing.
    setResults([])
    setSearched(false)
    setHighlight(-1)
    if (next.trim().length < 3) {
      setSearching(false)
      setOpen(false)
      return
    }
    setSearching(true)
    setOpen(true)
    debounceRef.current = window.setTimeout(() => search(next), 300)
  }

  function pick(r: GeocodeResult) {
    onChange(r)
    setText(r.label)
    setOpen(false)
    setHighlight(-1)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      setOpen(false)
      return
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (results.length === 0) return
      e.preventDefault()
      const delta = e.key === 'ArrowDown' ? 1 : -1
      setHighlight((h) => (h + delta + results.length) % results.length)
      setOpen(true)
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (results.length > 0) {
        pick(results[highlight >= 0 ? highlight : 0])
      } else if (text.trim().length >= 3) {
        // Search now instead of waiting out the debounce
        window.clearTimeout(debounceRef.current)
        search(text)
      }
    }
  }

  return (
    <div className="location-input">
      <span className={dotClass} />
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => handleInput(e.target.value)}
        // Select-all on focus: a filled field almost always gets replaced,
        // not appended to - typing straight away should just work.
        onFocus={(e) => {
          e.target.select()
          if (results.length > 0) setOpen(true)
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={handleKeyDown}
      />
      {open && (results.length > 0 || searching || searched) && (
        <ul className="location-results">
          {results.map((r, i) => (
            <li
              key={`${r.lat},${r.lon}`}
              className={i === highlight ? 'highlighted' : ''}
              onMouseDown={() => pick(r)}
              onMouseEnter={() => setHighlight(i)}
            >
              {r.label}
            </li>
          ))}
          {searching && results.length === 0 && <li className="location-empty">Searching…</li>}
          {!searching && searched && results.length === 0 && (
            <li className="location-empty">No matches. Leeway covers California for now.</li>
          )}
        </ul>
      )}
    </div>
  )
}
