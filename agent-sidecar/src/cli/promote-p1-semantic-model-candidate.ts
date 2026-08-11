import { createHash } from 'node:crypto'
import {
  lstatSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'

import {
  loadPiModelCandidate,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
} from '../pi/index.js'
import {
  FORMAL_P1_CERTIFIED_INPUT_SCOPE,
  FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
} from './formal-p1-sidecar.js'

type Json = Record<string, any>

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim() ?? ''
  if (!value) throw new Error(`缺少环境变量 ${name}`)
  return value
}

function regularFile(path: string): string {
  if (!isAbsolute(path)) throw new Error('晋级输入必须是绝对路径')
  const normalized = resolve(path)
  const stats = lstatSync(normalized)
  if (
    !stats.isFile() ||
    stats.isSymbolicLink() ||
    realpathSync(normalized) !== normalized
  ) {
    throw new Error('晋级输入必须是无符号链接普通文件')
  }
  return normalized
}

function sha256(value: Buffer | string): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`,
  ).join(',')}}`
}

function readJson(path: string): Json {
  const value = JSON.parse(readFileSync(regularFile(path), 'utf8')) as unknown
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('认证清单根节点无效')
  }
  return value as Json
}

async function main(): Promise<void> {
  const manifestPath = regularFile(
    requiredEnvironment('COUNTRY_OUTAGE_P1_SEMANTIC_CERTIFICATION_MANIFEST'),
  )
  const projectRoot = resolve(
    requiredEnvironment('COUNTRY_OUTAGE_P1_PROJECT_ROOT'),
  )
  const outputPath = resolve(
    requiredEnvironment('COUNTRY_OUTAGE_P1_CERTIFIED_REGISTRY_OUTPUT'),
  )
  if (!isAbsolute(projectRoot) || !isAbsolute(outputPath)) {
    throw new Error('项目根和注册表输出必须是绝对路径')
  }
  const manifest = readJson(manifestPath)
  const evidenceId = manifest.evidence_id
  const payload = { ...manifest }
  delete payload.evidence_id
  if (
    manifest.schema_version !==
      'country_outage_p1_semantic_model_certification_v1' ||
    manifest.status !== 'certified' ||
    typeof evidenceId !== 'string' ||
    evidenceId !==
      `evidence:p1-semantic-certification:${sha256(canonical(payload))}` ||
    manifest.scenario_set_id !== FORMAL_P1_CERTIFIED_SCENARIO_SET_ID ||
    manifest.input_scope !== FORMAL_P1_CERTIFIED_INPUT_SCOPE ||
    !Number.isFinite(Date.parse(manifest.certified_at)) ||
    !Number.isFinite(Date.parse(manifest.valid_until)) ||
    Date.now() >= Date.parse(manifest.valid_until) ||
    manifest.metrics?.user_goal_fidelity < 0.95 ||
    manifest.metrics?.grounding_legality_rate !== 1 ||
    manifest.per_call_cost_recorded !== true ||
    manifest.model_call_count !== manifest.case_count ||
    !Array.isArray(manifest.source_identity?.files) ||
    !Array.isArray(manifest.case_receipts) ||
    manifest.case_receipts.length !== manifest.case_count
  ) {
    throw new Error('P1 语义认证清单未达到晋级门')
  }
  const loadedCandidate = await loadPiModelCandidate(
    process.env.COUNTRY_OUTAGE_PI_CANDIDATE_PATH?.trim() || undefined,
  )
  if (
    manifest.candidate_id !== loadedCandidate.candidate.candidateId ||
    manifest.candidate_resource_sha256 !== loadedCandidate.resourceSha256 ||
    manifest.pi_version !== loadedCandidate.candidate.piVersion ||
    manifest.provider !== loadedCandidate.candidate.provider ||
    manifest.model !== loadedCandidate.candidate.model ||
    manifest.expected_response_model !==
      loadedCandidate.candidate.expectedResponseModel
  ) {
    throw new Error('P1 语义证书与当前模型候选不一致')
  }
  for (const item of manifest.source_identity.files) {
    if (
      !item ||
      typeof item.path !== 'string' ||
      typeof item.sha256 !== 'string' ||
      item.sha256 !==
        sha256(readFileSync(regularFile(resolve(projectRoot, item.path))))
    ) {
      throw new Error('P1 语义证书绑定源码已漂移')
    }
  }
  const certificationDirectory = dirname(manifestPath)
  for (const item of manifest.case_receipts) {
    if (
      !item ||
      typeof item.path !== 'string' ||
      typeof item.sha256 !== 'string' ||
      item.sha256 !==
        sha256(
          readFileSync(
            regularFile(resolve(certificationDirectory, item.path)),
          ),
        )
    ) {
      throw new Error('P1 语义认证逐案回执已漂移')
    }
  }
  const candidate = loadedCandidate.candidate
  const registry = {
    schemaVersion: 'country_outage_pi_certified_models_v1',
    registryVersion:
      `deepseek-v4-flash-p1-certified-${sha256(evidenceId).slice(0, 12)}`,
    status: 'frozen',
    profiles: [{
      id: candidate.candidateId,
      status: 'certified',
      provider: candidate.provider,
      model: candidate.model,
      modelVersion: candidate.modelVersion,
      expectedResponseModel: candidate.expectedResponseModel,
      thinkingLevel: candidate.thinkingLevel,
      piVersion: candidate.piVersion,
      certificationEvidenceId: evidenceId,
      certifiedAt: manifest.certified_at,
      modelRevisionKind: 'mutable_alias',
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: manifest.valid_until,
      certifiedScenarioSetId: FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
      certifiedInputScope: FORMAL_P1_CERTIFIED_INPUT_SCOPE,
    }],
  }
  writeFileSync(outputPath, `${JSON.stringify(registry, null, 2)}\n`, {
    mode: 0o600,
  })
  process.stdout.write(`${JSON.stringify({
    event: 'country_outage_p1_semantic_model_promoted',
    registryPath: outputPath,
    registrySha256: sha256(readFileSync(outputPath)),
    registryVersion: registry.registryVersion,
    profileId: candidate.candidateId,
    certificationEvidenceId: evidenceId,
    reportCapability: 'disabled',
  })}\n`)
}

void main().catch((error: unknown) => {
  process.stderr.write(`${JSON.stringify({
    event: 'country_outage_p1_semantic_model_promotion_failed',
    code: 'p1_semantic_promotion_failed',
    message: error instanceof Error ? error.message : 'P1 语义晋级失败',
  })}\n`)
  process.exitCode = 1
})
