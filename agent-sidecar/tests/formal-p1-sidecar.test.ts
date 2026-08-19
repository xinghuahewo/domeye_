import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  createFormalP1Sidecar,
  FORMAL_P1_SIDECAR_RETIREMENT_CODE,
  FormalP1SidecarRetiredError,
  startFormalP1Sidecar,
} from '../src/cli/formal-p1-sidecar.js'
import {
  FORMAL_P1_CERTIFIED_INPUT_SCOPE,
  FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
} from '../src/cli/retired-p1-semantic-certification.js'

const packageJsonPath = new URL('../../package.json', import.meta.url)
const formalSidecarSourcePath = new URL(
  '../../src/cli/formal-p1-sidecar.ts',
  import.meta.url,
)
const serveFormalP1SourcePath = new URL(
  '../../src/cli/serve-formal-p1.ts',
  import.meta.url,
)
const certifySourcePath = new URL(
  '../../src/cli/certify-p1-semantic-model-candidate.ts',
  import.meta.url,
)
const promoteSourcePath = new URL(
  '../../src/cli/promote-p1-semantic-model-candidate.ts',
  import.meta.url,
)
const certifyDistPath = new URL(
  '../src/cli/certify-p1-semantic-model-candidate.js',
  import.meta.url,
)
const promoteDistPath = new URL(
  '../src/cli/promote-p1-semantic-model-candidate.js',
  import.meta.url,
)

test('旧正式 P1 Sidecar 的构造与启动入口均显式失败关闭', async () => {
  const assertRetired = (error: unknown): boolean => {
    assert.ok(error instanceof FormalP1SidecarRetiredError)
    assert.equal(error.code, FORMAL_P1_SIDECAR_RETIREMENT_CODE)
    return true
  }

  await assert.rejects(createFormalP1Sidecar(), assertRetired)
  await assert.rejects(startFormalP1Sidecar(), assertRetired)
})

test('旧 P1 独立聊天及语义认证晋级命令不再是 package 活跃入口', () => {
  const packageJson = JSON.parse(
    readFileSync(packageJsonPath, 'utf8'),
  ) as { scripts?: Record<string, string> }
  const scripts = packageJson.scripts ?? {}

  assert.equal('start:formal:p1' in scripts, false)
  assert.equal('certify:model:p1-semantic' in scripts, false)
  assert.equal('promote:model:p1-semantic' in scripts, false)
})

test('退役启动文件不再构造旧路由或监听端口', () => {
  const formalSidecarSource = readFileSync(formalSidecarSourcePath, 'utf8')
  const serveFormalP1Source = readFileSync(serveFormalP1SourcePath, 'utf8')

  assert.doesNotMatch(formalSidecarSource, /\.\.\/chat|\.\.\/server/)
  assert.doesNotMatch(formalSidecarSource, /createServer|createCountryOutageAgentHttpHandler/)
  assert.doesNotMatch(formalSidecarSource, /\.listen\s*\(/)
  assert.doesNotMatch(serveFormalP1Source, /startFormalP1Sidecar|\.listen\s*\(/)
  assert.match(serveFormalP1Source, /country_outage_formal_p1_sidecar_retired/)
})

test('历史认证常量仅供旧制品识别', () => {
  assert.equal(
    FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
    'country-outage-p1-page-coverage-s2-v1',
  )
  assert.equal(
    FORMAL_P1_CERTIFIED_INPUT_SCOPE,
    'country_outage_p1_rrc25_event_bound_chat_v1',
  )
})

test('旧 P1 认证与晋级源码不再导入模型、计划、Grounding 或文件写入路径', () => {
  const sources = [
    readFileSync(certifySourcePath, 'utf8'),
    readFileSync(promoteSourcePath, 'utf8'),
  ]

  for (const source of sources) {
    assert.doesNotMatch(source, /\bfrom\s+['"][^'"]+['"]/)
    assert.doesNotMatch(
      source,
      /P1ModelUserGoalPlanner|P1PiSemanticModel|P1RuntimeV2Grounder|loadPiModelCandidate|createCandidatePiModelBinding/,
    )
    assert.doesNotMatch(
      source,
      /writeFileSync|mkdirSync|appendFileSync|createWriteStream/,
    )
    assert.match(source, /process\.exitCode\s*=\s*1/)
  }
})

function assertRetiredCli(
  distPath: URL,
  expectedEvent: string,
  expectedCode: string,
  expectedMessage: string,
): void {
  const workingDirectory = mkdtempSync(join(tmpdir(), 'domeye-retired-p1-cli-'))
  try {
    const result = spawnSync(process.execPath, [fileURLToPath(distPath)], {
      cwd: workingDirectory,
      encoding: 'utf8',
      env: {
        ...process.env,
        COUNTRY_OUTAGE_P1_PROJECT_ROOT: workingDirectory,
        COUNTRY_OUTAGE_P1_SEMANTIC_CERTIFICATION_DIRECTORY: join(
          workingDirectory,
          'certification',
        ),
        COUNTRY_OUTAGE_P1_CERTIFIED_REGISTRY_OUTPUT: join(
          workingDirectory,
          'registry.json',
        ),
      },
    })

    assert.equal(result.signal, null)
    assert.equal(result.status, 1)
    assert.equal(result.stdout, '')
    assert.deepEqual(JSON.parse(result.stderr.trim()), {
      event: expectedEvent,
      code: expectedCode,
      message: expectedMessage,
    })
    assert.deepEqual(readdirSync(workingDirectory), [])
  } finally {
    rmSync(workingDirectory, { recursive: true, force: true })
  }
}

test('旧 P1 认证与晋级 dist CLI 均以固定机器事件失败关闭且不写文件', () => {
  assertRetiredCli(
    certifyDistPath,
    'country_outage_p1_semantic_model_certification_retired',
    'p1_semantic_certification_retired',
    '旧 P1 语义模型认证命令已退役',
  )
  assertRetiredCli(
    promoteDistPath,
    'country_outage_p1_semantic_model_promotion_retired',
    'p1_semantic_promotion_retired',
    '旧 P1 语义模型晋级命令已退役',
  )
})
