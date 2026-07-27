// Keeps the iOS build free of any Node dependency: Capacitor's generated
// Package.swift points at plugin packages inside node_modules, which only
// exist after `npm ci` - a handoff machine with just Xcode couldn't resolve
// them. This copies the Swift plugin packages into ios/vendor (committed)
// and re-points Package.swift there. Runs automatically at the end of
// `npm run sync:ios`, since every `cap sync` regenerates the node_modules
// paths.
import { cpSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const vendorDir = join(root, 'ios', 'vendor')
const plugins = ['local-notifications', 'share', 'status-bar']

for (const p of plugins) {
  const src = join(root, 'node_modules', '@capacitor', p)
  const dst = join(vendorDir, p)
  rmSync(dst, { recursive: true, force: true })
  cpSync(src, dst, { recursive: true })
}

const manifest = join(root, 'ios', 'App', 'CapApp-SPM', 'Package.swift')
const rewritten = readFileSync(manifest, 'utf8').replaceAll(
  '../../../node_modules/@capacitor/',
  '../../vendor/',
)
writeFileSync(manifest, rewritten)
console.log(`vendored ${plugins.length} plugin packages into ios/vendor and re-pointed Package.swift`)
