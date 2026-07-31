import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'

const IMPORT_SPECIFIER =
  /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/g

function sourceDependencyGraph(entry: string): {
  files: Set<string>
  builtins: Set<string>
} {
  const files = new Set<string>()
  const builtins = new Set<string>()
  const pending = [entry]
  while (pending.length > 0) {
    const current = pending.pop()!
    if (files.has(current)) continue
    files.add(current)
    const source = readFileSync(current, 'utf8')
    for (const match of source.matchAll(IMPORT_SPECIFIER)) {
      const specifier = match[1]!
      if (specifier.startsWith('node:')) {
        builtins.add(specifier)
        continue
      }
      if (!specifier.startsWith('.')) continue
      const candidate = resolve(dirname(current), specifier)
      if (existsSync(candidate)) pending.push(candidate)
    }
  }
  return { files, builtins }
}

function normalizedFiles(graph: { files: Set<string> }): string[] {
  return [...graph.files].map(
    (file) => file.replaceAll('\\', '/'),
  )
}

test('Core 入口依赖图不包含 application 或 external 能力包', () => {
  const entry = resolve(
    import.meta.dirname,
    '../src/core/index.js',
  )
  const graph = sourceDependencyGraph(entry)
  const files = normalizedFiles(graph)
  assert.equal(
    files.some(
      (file) =>
        file.includes('/application/') ||
        file.includes('/external/'),
    ),
    false,
  )
})

test('完整正式入口依赖图不包含外部来源 DNS/公网直连实现', () => {
  const entry = resolve(
    import.meta.dirname,
    '../src/cli/serve-formal.js',
  )
  const graph = sourceDependencyGraph(entry)
  const files = normalizedFiles(graph)
  assert.equal(
    files.some(
      (file) =>
        file.endsWith('/external/safe-http-transport.js') ||
        file.endsWith(
          '/external/safe-external-evidence-service.js',
        ),
    ),
    false,
  )
  assert.equal(graph.builtins.has('node:dns'), false)
  assert.equal(graph.builtins.has('node:dns/promises'), false)
  assert.equal(graph.builtins.has('node:https'), false)
})
