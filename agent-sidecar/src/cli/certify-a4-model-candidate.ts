import {
  PiModelCertificationError,
  runA4ModelCandidateCertification,
} from '../pi/index.js'

class CandidateCertificationConfigurationError extends Error {
  constructor() {
    super('DeepSeek 候选完整报告认证配置无效')
    this.name = 'CandidateCertificationConfigurationError'
  }
}

function requiredEnvironmentValue(name: string): string {
  const value = process.env[name]?.trim() ?? ''
  if (!value) throw new CandidateCertificationConfigurationError()
  return value
}

function optionalPositiveInteger(name: string): number | undefined {
  const raw = process.env[name]?.trim()
  if (!raw) return undefined
  const value = Number(raw)
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new CandidateCertificationConfigurationError()
  }
  return value
}

async function main(): Promise<void> {
  const domeyeApiTimeoutMs = optionalPositiveInteger(
    'DOMEYE_API_TIMEOUT_MS',
  )
  const pdfTimeoutMs = optionalPositiveInteger(
    'DOMEYE_REPORT_PDF_TIMEOUT_MS',
  )
  const result = await runA4ModelCandidateCertification({
    authPath: requiredEnvironmentValue(
      'COUNTRY_OUTAGE_PI_CANDIDATE_AUTH_PATH',
    ),
    domeyeApiBaseUrl: requiredEnvironmentValue(
      'DOMEYE_API_BASE_URL',
    ),
    pythonExecutable: requiredEnvironmentValue(
      'DOMEYE_REPORT_PYTHON_EXECUTABLE',
    ),
    fontPath: requiredEnvironmentValue('DOMEYE_REPORT_FONT_PATH'),
    ...(domeyeApiTimeoutMs === undefined
      ? {}
      : { domeyeApiTimeoutMs }),
    ...(pdfTimeoutMs === undefined ? {} : { pdfTimeoutMs }),
  })
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_a4_model_certification_completed',
      evidenceId: result.evidenceId,
      artifactDirectory: result.artifactDirectory,
      runtimeIdentity: result.manifest.runtimeIdentity,
      provenance: result.manifest.provenance,
      actualCertificationCostCny:
        result.manifest.budget.actualCertificationCostCny,
      promoted: false,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const code =
    error instanceof PiModelCertificationError
      ? error.code
      : error instanceof CandidateCertificationConfigurationError
        ? 'candidate_certification_configuration_invalid'
        : 'candidate_certification_failed'
  const message =
    error instanceof PiModelCertificationError ||
    error instanceof CandidateCertificationConfigurationError
      ? error.message
      : 'DeepSeek 候选完整报告认证失败'
  // 只输出固定错误码和安全文案，不回显认证路径、密钥、提示词、模型原始响应或工具参数。
  process.stderr.write(
    `${JSON.stringify({
      event: 'country_outage_a4_model_certification_failed',
      code,
      message,
    })}\n`,
  )
  process.exitCode = 1
})
