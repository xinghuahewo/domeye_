import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import type { CountryOutageReportDocument } from './contracts.js'

export const COUNTRY_OUTAGE_PDF_MAX_BYTES = 10 * 1024 * 1024
export const COUNTRY_OUTAGE_PDF_MAX_PAGES = 40
export const COUNTRY_OUTAGE_PDF_DEFAULT_TIMEOUT_MS = 20_000

const MAX_STDERR_BYTES = 64 * 1024
const SCRIPT_PATH = fileURLToPath(
  new URL('../../../scripts/render_country_outage_report.py', import.meta.url),
)

export interface CountryOutagePdfRendererConfig {
  /** Trusted executable configured when the sidecar starts; never model input. */
  pythonExecutable: string
  /** Trusted TTF/OTF path configured when the sidecar starts; never model input. */
  fontPath: string
  timeoutMs?: number
}

export class PdfRenderConfigurationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PdfRenderConfigurationError'
  }
}

export class PdfRenderProcessError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PdfRenderProcessError'
  }
}

export class PdfRenderTimeoutError extends Error {
  constructor(readonly timeoutMs: number) {
    super(`PDF 生成超过 ${timeoutMs}ms，已终止`)
    this.name = 'PdfRenderTimeoutError'
  }
}

export class PdfRenderAbortedError extends Error {
  constructor() {
    super('PDF 生成已取消')
    this.name = 'PdfRenderAbortedError'
  }
}

export class PdfRenderSizeLimitError extends Error {
  constructor(readonly maxBytes: number) {
    super(`PDF 生成结果超过 ${maxBytes} 字节限制`)
    this.name = 'PdfRenderSizeLimitError'
  }
}

function validateConfig(config: CountryOutagePdfRendererConfig): number {
  if (!config.pythonExecutable.trim()) {
    throw new PdfRenderConfigurationError('pythonExecutable 不能为空')
  }
  if (!config.fontPath.trim()) {
    throw new PdfRenderConfigurationError('fontPath 不能为空')
  }
  const timeoutMs =
    config.timeoutMs ?? COUNTRY_OUTAGE_PDF_DEFAULT_TIMEOUT_MS
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new PdfRenderConfigurationError('timeoutMs 必须是正整数')
  }
  return timeoutMs
}

function terminate(child: ChildProcessWithoutNullStreams): void {
  if (!child.killed) child.kill('SIGKILL')
}

export class CountryOutagePdfRenderer {
  readonly #pythonExecutable: string
  readonly #fontPath: string
  readonly #timeoutMs: number

  constructor(config: CountryOutagePdfRendererConfig) {
    this.#timeoutMs = validateConfig(config)
    this.#pythonExecutable = config.pythonExecutable
    this.#fontPath = config.fontPath
  }

  render(
    document: CountryOutageReportDocument,
    signal?: AbortSignal,
  ): Promise<Buffer> {
    if (signal?.aborted) {
      return Promise.reject(new PdfRenderAbortedError())
    }

    const input = Buffer.from(
      JSON.stringify({
        document,
        fontPath: this.#fontPath,
      }),
      'utf8',
    )
    if (input.byteLength > COUNTRY_OUTAGE_PDF_MAX_BYTES) {
      return Promise.reject(
        new PdfRenderSizeLimitError(COUNTRY_OUTAGE_PDF_MAX_BYTES),
      )
    }

    return new Promise<Buffer>((resolve, reject) => {
      const child = spawn(this.#pythonExecutable, [SCRIPT_PATH], {
        shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      })
      const stdoutChunks: Buffer[] = []
      const stderrChunks: Buffer[] = []
      let stdoutBytes = 0
      let stderrBytes = 0
      let terminalError: Error | undefined
      let settled = false

      const finish = (error?: Error, pdf?: Buffer): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        signal?.removeEventListener('abort', abort)
        if (error) reject(error)
        else resolve(pdf as Buffer)
      }

      const abort = (): void => {
        terminalError ??= new PdfRenderAbortedError()
        terminate(child)
      }
      signal?.addEventListener('abort', abort, { once: true })

      const timer = setTimeout(() => {
        terminalError ??= new PdfRenderTimeoutError(this.#timeoutMs)
        terminate(child)
      }, this.#timeoutMs)
      timer.unref()

      child.stdout.on('data', (chunk: Buffer) => {
        if (terminalError) return
        stdoutBytes += chunk.byteLength
        if (stdoutBytes > COUNTRY_OUTAGE_PDF_MAX_BYTES) {
          terminalError = new PdfRenderSizeLimitError(
            COUNTRY_OUTAGE_PDF_MAX_BYTES,
          )
          terminate(child)
          return
        }
        stdoutChunks.push(chunk)
      })

      child.stderr.on('data', (chunk: Buffer) => {
        if (stderrBytes >= MAX_STDERR_BYTES) return
        const remaining = MAX_STDERR_BYTES - stderrBytes
        const accepted =
          chunk.byteLength <= remaining ? chunk : chunk.subarray(0, remaining)
        stderrChunks.push(accepted)
        stderrBytes += accepted.byteLength
      })

      child.once('error', (error) => {
        finish(
          new PdfRenderProcessError(
            `无法启动受信任的 PDF 渲染进程：${error.message}`,
          ),
        )
      })

      child.once('close', (code, closeSignal) => {
        if (terminalError) {
          finish(terminalError)
          return
        }
        if (code !== 0) {
          const stderr = Buffer.concat(stderrChunks).toString('utf8').trim()
          const detail = stderr || `signal=${closeSignal ?? 'none'}`
          finish(
            new PdfRenderProcessError(
              `PDF 渲染进程退出码 ${code ?? 'null'}：${detail}`,
            ),
          )
          return
        }
        const pdf = Buffer.concat(stdoutChunks)
        if (pdf.byteLength === 0 || !pdf.subarray(0, 5).equals(Buffer.from('%PDF-'))) {
          finish(new PdfRenderProcessError('PDF 渲染进程未返回有效 PDF'))
          return
        }
        finish(undefined, pdf)
      })

      child.stdin.once('error', (error) => {
        if (!terminalError) {
          terminalError = new PdfRenderProcessError(
            `无法向 PDF 渲染进程写入报告：${error.message}`,
          )
          terminate(child)
        }
      })
      child.stdin.end(input)
    })
  }
}
