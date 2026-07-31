import {
  createCandidatePiModelBinding,
  loadA4ProviderPriceAttestation,
  loadPiModelCandidate,
  PiModelCertificationError,
} from '../pi/index.js'

async function main(): Promise<void> {
  const authPath =
    process.env.COUNTRY_OUTAGE_PI_CANDIDATE_AUTH_PATH?.trim() ?? ''
  const loadedCandidate = await loadPiModelCandidate()
  const priceAttestation = loadA4ProviderPriceAttestation({
    loadedCandidate,
  })
  const binding = await createCandidatePiModelBinding({
    loadedCandidate,
    authPath,
    priceAttestation,
  })
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_pi_model_candidate_preflight',
      preflight: binding.preflight,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const code =
    error instanceof PiModelCertificationError
      ? error.code
      : 'candidate_preflight_failed'
  const message =
    error instanceof Error
      ? error.message
      : 'DeepSeek 候选模型预检失败'
  // 仅输出固定错误码与安全文案；不输出认证路径、凭据或底层异常。
  process.stderr.write(
    `${JSON.stringify({
      event: 'country_outage_pi_model_candidate_preflight_failed',
      code,
      message,
    })}\n`,
  )
  process.exitCode = 1
})
