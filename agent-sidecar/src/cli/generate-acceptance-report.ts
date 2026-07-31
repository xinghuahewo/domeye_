import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import { DomeyeCountryOutageClient } from '../domain/domeye-client.js'
import {
  loadFormalCountryOutageAcceptanceRuntime,
} from '../formal-acceptance-runtime.js'
import { CountryOutageArtifactBuilder } from '../report/artifact-builder.js'
import {
  createCountryOutageReportAuditManifestArtifact,
  describeCountryOutageReportAuditManifestArtifact,
} from '../report/audit-manifest.js'
import { DeterministicAcceptanceNarrator } from '../report/deterministic-narrator.js'
import { CountryOutagePdfRenderer } from '../report/pdf-renderer.js'
import { CountryOutageReportCompiler } from '../report/report-compiler.js'
import {
  assertAcceptanceReportMatchesRepresentativeEvent,
  assertAcceptanceReportReference,
} from './acceptance-report-binding.js'

interface CliOptions {
  baseUrl: string
  reference: string
  outputDirectory: string
  pythonExecutable: string
  fontPath: string
  generatedAt?: Date
}

function parseArguments(values: string[]): CliOptions {
  const options = new Map<string, string>()
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index]
    const value = values[index + 1]
    if (!key?.startsWith('--') || !value) {
      throw new Error(`参数必须使用 --name value：${key ?? '缺少参数'}`)
    }
    options.set(key, value)
  }
  const required = (
    key: string,
    environmentName?: string,
  ): string => {
    const value =
      options.get(key) ??
      (environmentName ? process.env[environmentName] : undefined)
    if (!value) throw new Error(`缺少必需参数 ${key}`)
    return value
  }
  const generatedAtValue = options.get('--generated-at')
  const generatedAt = generatedAtValue
    ? new Date(generatedAtValue)
    : undefined
  if (generatedAt && Number.isNaN(generatedAt.valueOf())) {
    throw new Error('--generated-at 必须是有效的 ISO 8601 时间')
  }
  return {
    baseUrl: required('--base-url', 'DOMEYE_API_BASE_URL'),
    reference: required('--reference'),
    outputDirectory: resolve(required('--output-dir')),
    pythonExecutable: required(
      '--python',
      'DOMEYE_REPORT_PYTHON_EXECUTABLE',
    ),
    fontPath: required('--font', 'DOMEYE_REPORT_FONT_PATH'),
    ...(generatedAt ? { generatedAt } : {}),
  }
}

async function main(): Promise<void> {
  const options = parseArguments(process.argv.slice(2))
  const acceptanceRuntime =
    loadFormalCountryOutageAcceptanceRuntime()
  assertAcceptanceReportReference(
    options.reference,
    acceptanceRuntime,
  )
  const client = new DomeyeCountryOutageClient({
    baseUrl: options.baseUrl,
    timeoutMs: 10_000,
    maximumSnapshotBatchRetries: 2,
  })
  const compiler = new CountryOutageReportCompiler({
    client,
    narrator: new DeterministicAcceptanceNarrator(),
    asnPageSize: 10,
    ...(options.generatedAt
      ? { now: () => new Date(options.generatedAt!) }
      : {}),
  })
  const compiled = await compiler.compileWithEvidence(options.reference)
  assertAcceptanceReportMatchesRepresentativeEvent(
    compiled,
    acceptanceRuntime,
  )
  const { document } = compiled
  const builder = new CountryOutageArtifactBuilder(
    new CountryOutagePdfRenderer({
      pythonExecutable: options.pythonExecutable,
      fontPath: options.fontPath,
      timeoutMs: 20_000,
    }),
  )
  const result = await builder.build(document)
  const auditManifest =
    createCountryOutageReportAuditManifestArtifact(compiled)
  await mkdir(options.outputDirectory, { recursive: true })
  const formats = [result.markdown, result.pdf]
  for (const outcome of formats) {
    if (outcome.status === 'ready') {
      await writeFile(
        resolve(options.outputDirectory, outcome.artifact.filename),
        outcome.artifact.content,
      )
    }
  }
  const manifest = {
    schemaVersion: 'country_outage_acceptance_artifacts_v1',
    artifactId: result.artifactId,
    event: document.event,
    snapshot: document.snapshot,
    factSetId: document.factSetId,
    reportContentSha256: document.reportContentSha256,
    reportSpecificationVersion: document.reportSpecificationVersion,
    projectKnowledgeVersion: document.projectKnowledgeVersion,
    validatorRulesVersion: document.validatorRulesVersion,
    skillBundleSha256: document.skillBundleSha256,
    model: document.model,
    validation: document.validation,
    auditManifest:
      describeCountryOutageReportAuditManifestArtifact(auditManifest),
    formats: {
      markdown:
        result.markdown.status === 'ready'
          ? {
              status: 'ready',
              filename: result.markdown.artifact.filename,
              byteLength: result.markdown.artifact.byteLength,
              sha256: result.markdown.artifact.sha256,
            }
          : result.markdown,
      pdf:
        result.pdf.status === 'ready'
          ? {
              status: 'ready',
              filename: result.pdf.artifact.filename,
              byteLength: result.pdf.artifact.byteLength,
              sha256: result.pdf.artifact.sha256,
            }
          : result.pdf,
    },
  }
  await Promise.all([
    writeFile(
      resolve(options.outputDirectory, 'report-document.json'),
      `${JSON.stringify(document, null, 2)}\n`,
      'utf8',
    ),
    writeFile(
      resolve(options.outputDirectory, 'manifest.json'),
      `${JSON.stringify(manifest, null, 2)}\n`,
      'utf8',
    ),
    writeFile(
      resolve(
        options.outputDirectory,
        auditManifest.artifact.filename,
      ),
      auditManifest.artifact.content,
    ),
  ])
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`)
  if (formats.some((outcome) => outcome.status === 'failed')) {
    process.exitCode = 2
  }
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断报告验收生成失败：${message}\n`)
  process.exitCode = 1
})
