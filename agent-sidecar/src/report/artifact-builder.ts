import { createHash } from 'node:crypto'

import type {
  CountryOutageReportDocument,
  ReportArtifact,
  ReportArtifactBuildResult,
  ReportArtifactFailure,
  ReportArtifactOutcome,
} from './contracts.js'
import { renderReportMarkdown } from './markdown-renderer.js'

export const COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES = 2 * 1024 * 1024

export interface PdfDocumentRenderer {
  render(
    document: CountryOutageReportDocument,
    signal?: AbortSignal,
  ): Promise<Buffer>
}

export class ArtifactBuildInputError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ArtifactBuildInputError'
  }
}

function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex')
}

function compactUtc(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return 'unknown-window'
  return parsed.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')
}

function safeSegment(value: string): string {
  const safe = value
    .normalize('NFKC')
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return safe || 'unknown'
}

function filenameBase(document: CountryOutageReportDocument): string {
  return [
    safeSegment(document.event.country_code.toUpperCase()),
    'country-outage',
    compactUtc(document.snapshot.windowStartUtc),
    compactUtc(document.snapshot.windowEndUtc),
    `r${document.snapshot.revision}`,
    compactUtc(document.generatedAt),
  ].join('_')
}

function artifact(
  document: CountryOutageReportDocument,
  format: ReportArtifact['format'],
  content: Buffer,
): ReportArtifact {
  return {
    format,
    filename: `${filenameBase(document)}.${format === 'markdown' ? 'md' : 'pdf'}`,
    mediaType:
      format === 'markdown'
        ? 'text/markdown; charset=utf-8'
        : 'application/pdf',
    byteLength: content.byteLength,
    sha256: sha256(content),
    content,
  }
}

function failure(
  format: ReportArtifactFailure['format'],
  error: unknown,
): ReportArtifactOutcome {
  const value = error instanceof Error ? error : new Error('未知制品生成错误')
  return {
    status: 'failed',
    error: {
      format,
      code: value.name || 'ArtifactBuildError',
      message: value.message,
    },
  }
}

function buildMarkdown(
  document: CountryOutageReportDocument,
): ReportArtifactOutcome {
  try {
    const content = Buffer.from(renderReportMarkdown(document), 'utf8')
    if (content.byteLength > COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES) {
      throw new ArtifactBuildInputError(
        `Markdown 超过 ${COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES} 字节限制`,
      )
    }
    return { status: 'ready', artifact: artifact(document, 'markdown', content) }
  } catch (error) {
    return failure('markdown', error)
  }
}

async function buildPdf(
  document: CountryOutageReportDocument,
  renderer: PdfDocumentRenderer,
  signal?: AbortSignal,
): Promise<ReportArtifactOutcome> {
  try {
    const content = await renderer.render(document, signal)
    return { status: 'ready', artifact: artifact(document, 'pdf', content) }
  } catch (error) {
    return failure('pdf', error)
  }
}

export class CountryOutageArtifactBuilder {
  readonly #pdfRenderer: PdfDocumentRenderer

  constructor(pdfRenderer: PdfDocumentRenderer) {
    this.#pdfRenderer = pdfRenderer
  }

  async build(
    document: CountryOutageReportDocument,
    signal?: AbortSignal,
  ): Promise<ReportArtifactBuildResult> {
    if (!document.validation.passed) {
      throw new ArtifactBuildInputError('未通过机器校验的报告不能生成下载制品')
    }
    const markdown = buildMarkdown(document)
    const pdf = await buildPdf(document, this.#pdfRenderer, signal)
    return {
      artifactId: document.artifactId,
      markdown,
      pdf,
    }
  }
}
