import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire, registerHooks } from 'node:module'
import { relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const agentSourceMarker = '/agent-sidecar/src/'
const requireFromSidecar = createRequire(
  new URL('../../../agent-sidecar/package.json', import.meta.url),
)
const typescript = requireFromSidecar('typescript')
const loadedSources = new Map()

/**
 * 评测必须执行 Candidate 摘要绑定的 TypeScript 源码，不能执行 gitignored 的 dist。
 * 这个窄加载器只处理 agent-sidecar/src 下的模块，并把源码在内存中转译为 ESM。
 */
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context)
    } catch (error) {
      if (
        error?.code === 'ERR_MODULE_NOT_FOUND'
        && specifier.endsWith('.js')
        && context.parentURL?.includes(agentSourceMarker)
      ) {
        const sourceUrl = new URL(
          specifier.replace(/\.js$/, '.ts'),
          context.parentURL,
        )
        if (existsSync(fileURLToPath(sourceUrl))) {
          return { url: sourceUrl.href, shortCircuit: true }
        }
      }
      throw error
    }
  },
  load(url, context, nextLoad) {
    if (url.includes(agentSourceMarker) && url.endsWith('.ts')) {
      const source = readFileSync(fileURLToPath(url), 'utf8')
      loadedSources.set(
        resolve(fileURLToPath(url)),
        `sha256:${createHash('sha256').update(source).digest('hex')}`,
      )
      const output = typescript.transpileModule(source, {
        compilerOptions: {
          module: typescript.ModuleKind.ES2022,
          target: typescript.ScriptTarget.ES2022,
          sourceMap: false,
          inlineSourceMap: false,
        },
        fileName: fileURLToPath(url),
        reportDiagnostics: false,
      })
      return {
        format: 'module',
        source: output.outputText,
        shortCircuit: true,
      }
    }
    return nextLoad(url, context)
  },
})

export const SOURCE_RUNTIME_LOADER_ID = Object.freeze({
  schema_version: 'domeye_evaluation_source_runtime_loader_v1',
  compiler: 'typescript',
  compiler_version: typescript.version,
  source_scope: 'agent-sidecar/src',
  emits_to_disk: false,
})

export function loadedAgentSourceClosure(projectRoot) {
  const root = resolve(projectRoot)
  return Object.freeze([...loadedSources.entries()]
    .map(([path, sha256]) => Object.freeze({
      path: relative(root, path).split('\\').join('/'),
      sha256,
    }))
    .sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0))
}
