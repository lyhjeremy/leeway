import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import './App.css'

// Stage 0: no real routing yet. A hardcoded LA -> SF route just proves the
// map, deploy pipeline, and (once live) the backend are wired up correctly.
const DUMMY_ROUTE: [number, number][] = [
  [-118.3965, 34.0211], // Culver City
  [-119.6982, 35.3733], // Buttonwillow
  [-121.9018, 36.9741], // Los Banos area
  [-122.4194, 37.7599], // Mission District, SF
]

const API_BASE = import.meta.env.VITE_API_BASE ?? 'https://leeway-api.onrender.com'

function App() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const [apiStatus, setApiStatus] = useState<'checking' | 'ok' | 'down'>('checking')

  useEffect(() => {
    if (!mapContainer.current) return

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [-120.5, 36.2],
      zoom: 5.6,
    })

    map.on('load', () => {
      map.addSource('dummy-route', {
        type: 'geojson',
        data: {
          type: 'Feature',
          properties: {},
          geometry: { type: 'LineString', coordinates: DUMMY_ROUTE },
        },
      })
      map.addLayer({
        id: 'dummy-route-line',
        type: 'line',
        source: 'dummy-route',
        paint: { 'line-color': '#2f2e2b', 'line-width': 5 },
      })

      new maplibregl.Marker({ color: '#0b0b0b' })
        .setLngLat(DUMMY_ROUTE[0])
        .addTo(map)
      new maplibregl.Marker({ color: '#0b0b0b' })
        .setLngLat(DUMMY_ROUTE[DUMMY_ROUTE.length - 1])
        .addTo(map)
    })

    return () => map.remove()
  }, [])

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => (r.ok ? setApiStatus('ok') : setApiStatus('down')))
      .catch(() => setApiStatus('down'))
  }, [])

  return (
    <div className="app-root">
      <header className="app-header">
        <span className="logo-dot" />
        <span className="logo-word">Leeway</span>
        <span className="tag">the second opinion before you leave</span>
        <span className={`api-chip api-chip--${apiStatus}`}>
          backend: {apiStatus === 'checking' ? 'checking…' : apiStatus === 'ok' ? 'connected' : 'unreachable'}
        </span>
      </header>
      <div ref={mapContainer} className="map" />
    </div>
  )
}

export default App
