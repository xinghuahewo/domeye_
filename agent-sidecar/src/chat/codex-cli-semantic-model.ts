import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { isAbsolute, join } from 'node:path'

import type { P1RawSemanticModel } from './runtime-v2-semantic.js'

const SAFE_MODEL = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export interface P1CodexCliSemanticModelOptions {
  executable: string
  model: string
  timeoutMs?: number
}

/**
 * 仅供本机 S2 开发验收使用。生产入口使用固定 API/Pi 模型绑定，不依赖桌面登录态。
 */
export class P1CodexCliSemanticModel implements P1RawSemanticModel {
  readonly identity: string
  readonly #executable: string
  readonly #model: string
  readonly #timeoutMs: number
  #active = false

  constructor(options: P1CodexCliSemanticModelOptions) {
    if (!isAbsolute(options.executable)) {
      throw new Error('codex_cli_executable_must_be_absolute')
    }
    if (!SAFE_MODEL.test(options.model)) {
      throw new Error('codex_cli_model_invalid')
    }
    this.#executable = options.executable
    this.#model = options.model
    this.#timeoutMs = options.timeoutMs ?? 180_000
    if (!Number.isSafeInteger(this.#timeoutMs) || this.#timeoutMs < 10_000) {
      throw new Error('codex_cli_timeout_invalid')
    }
    this.identity = `codex-cli:0.147.0-alpha.6.5:${this.#model}:blind-v2`
  }

  async complete(prompt: string, signal?: AbortSignal): Promise<string> {
    if (this.#active) throw new Error('codex_cli_semantic_busy')
    signal?.throwIfAborted()
    this.#active = true
    const isolatedDirectory = mkdtempSync(
      join(tmpdir(), 'domeye-p1-semantic-'),
    )
    try {
      return await new Promise<string>((resolve, reject) => {
        const child = spawn(
          this.#executable,
          [
            'exec',
            '--ephemeral',
            '--sandbox', 'read-only',
            '--ignore-user-config',
            '--ignore-rules',
            '--skip-git-repo-check',
            '--model', this.#model,
            '--json',
            '-',
          ],
          {
            cwd: isolatedDirectory,
            env: {
              PATH: process.env.PATH ?? '/usr/bin:/bin',
              HOME: process.env.HOME ?? '',
              USER: process.env.USER ?? '',
              TMPDIR: process.env.TMPDIR ?? tmpdir(),
              LANG: process.env.LANG ?? 'zh_CN.UTF-8',
              ...(process.env.LC_ALL
                ? { LC_ALL: process.env.LC_ALL }
                : {}),
            },
            stdio: ['pipe', 'pipe', 'pipe'],
          },
        )
        let stdout = ''
        let stderr = ''
        let settled = false
        const finish = (error?: Error, value?: string): void => {
          if (settled) return
          settled = true
          clearTimeout(timeout)
          signal?.removeEventListener('abort', onAbort)
          if (error) reject(error)
          else resolve(value ?? '')
        }
        const terminate = (): void => {
          if (child.exitCode === null) child.kill('SIGTERM')
        }
        const onAbort = (): void => {
          terminate()
          finish(new DOMException('本轮已取消', 'AbortError'))
        }
        const timeout = setTimeout(() => {
          terminate()
          finish(new Error('codex_cli_semantic_timeout'))
        }, this.#timeoutMs)
        timeout.unref()
        signal?.addEventListener('abort', onAbort, { once: true })
        child.stdout.setEncoding('utf8')
        child.stderr.setEncoding('utf8')
        child.stdout.on('data', (chunk: string) => {
          stdout += chunk
          if (Buffer.byteLength(stdout, 'utf8') > 1_048_576) {
            terminate()
            finish(new Error('codex_cli_event_stream_too_large'))
          }
        })
        child.stderr.on('data', (chunk: string) => {
          stderr += chunk
          if (Buffer.byteLength(stderr, 'utf8') > 262_144) {
            terminate()
            finish(new Error('codex_cli_stderr_too_large'))
          }
        })
        child.once('error', (error) => finish(error))
        child.once('close', (code) => {
          if (settled) return
          if (code !== 0) {
            finish(new Error('codex_cli_semantic_failed'))
            return
          }
          try {
            const events = stdout
              .split('\n')
              .filter(Boolean)
              .map((line) => JSON.parse(line) as Record<string, unknown>)
            const forbidden = events.some((event) => {
              const item = event.item
              if (!item || typeof item !== 'object' || Array.isArray(item)) {
                return false
              }
              const type = (item as Record<string, unknown>).type
              return type !== 'agent_message'
                && type !== 'error'
                && type !== 'reasoning'
            })
            if (forbidden) {
              finish(new Error('codex_cli_tool_activity_forbidden'))
              return
            }
            const messages = events
              .map((event) => event.item)
              .filter((item): item is Record<string, unknown> =>
                Boolean(item) && typeof item === 'object' && !Array.isArray(item)
              )
              .filter((item) =>
                item.type === 'agent_message' && typeof item.text === 'string'
              )
            if (messages.length !== 1) {
              finish(new Error('codex_cli_semantic_message_count_invalid'))
              return
            }
            finish(undefined, messages[0]!.text as string)
          } catch {
            finish(new Error('codex_cli_event_stream_invalid'))
          }
        })
        child.stdin.end(prompt, 'utf8')
      })
    } finally {
      this.#active = false
      rmSync(isolatedDirectory, { recursive: true, force: true })
    }
  }
}
