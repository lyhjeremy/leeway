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
  const [open, setOpen] = useState(false)
  const debounceRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    setText(value?.label ?? '')
  }, [value])

  function handleInput(next: string) {
    setText(next)
    onChange(null)
    window.clearTimeout(debounceRef.current)
    if (next.trim().length < 3) {
      setResults([])
      return
    }
    debounceRef.current = window.setTimeout(async () => {
      try {
        const r = await geocode(next)
        setResults(r)
        setOpen(true)
      } catch {
        setResults([])
      }
    }, 350)
  }

  function pick(r: GeocodeResult) {
    onChange(r)
    setText(r.label)
    setOpen(false)
  }

  return (
    <div className="location-input">
      <span className={dotClass} />
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => handleInput(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && results.length > 0 && (
        <ul className="location-results">
          {results.map((r) => (
            <li key={`${r.lat},${r.lon}`} onMouseDown={() => pick(r)}>
              {r.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
