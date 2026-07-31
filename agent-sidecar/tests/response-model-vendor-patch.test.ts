import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync, realpathSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { VERSION as CODING_AGENT_VERSION } from '@earendil-works/pi-coding-agent'

const PATCHED_SHA256 =
  '5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b'
const PATCH_ARTIFACT_SHA256 =
  'c62983d07f150ddbef0e412feb596406648f1e151430f633f406ca018e2412cd'
const PATCH_MANIFEST_SHA256 =
  '886b0faf7ccbd0dec19ba74aaa3d92e5b6a218177bf36f1f50a5ece553f8bfba'

function sha256(value: Buffer | string): string {
  return createHash('sha256').update(value).digest('hex')
}

test('受控 responseModel vendor patch 的版本、制品和安装源码摘要全部固定', () => {
  assert.equal(CODING_AGENT_VERSION, '0.82.1')
  const codingAgentEntry = realpathSync(
    fileURLToPath(
      import.meta.resolve('@earendil-works/pi-coding-agent'),
    ),
  )
  const codingAgentRoot = resolve(dirname(codingAgentEntry), '..')
  const piAiMetadata = JSON.parse(
    readFileSync(
      resolve(
        codingAgentRoot,
        'node_modules/@earendil-works/pi-ai/package.json',
      ),
      'utf8',
    ),
  ) as { name: string; version: string }
  assert.deepEqual(piAiMetadata, {
    ...piAiMetadata,
    name: '@earendil-works/pi-ai',
    version: '0.82.1',
  })

  const adapterSource = readFileSync(
    resolve(
      codingAgentRoot,
      'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',
    ),
    'utf8',
  )
  assert.equal(sha256(adapterSource), PATCHED_SHA256)
  assert.match(
    adapterSource,
    /if \(typeof chunk\.model === "string" && chunk\.model\.length > 0\) \{/u,
  )
  assert.doesNotMatch(
    adapterSource,
    /chunk\.model !== model\.id/u,
  )

  const manifestBytes = readFileSync(
    resolve(
      process.cwd(),
      'resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
    ),
  )
  const patchBytes = readFileSync(
    resolve(
      process.cwd(),
      'vendor-patches/pi-ai-0.82.1-openai-completions-response-model-v1.patch',
    ),
  )
  assert.equal(sha256(manifestBytes), PATCH_MANIFEST_SHA256)
  assert.equal(sha256(patchBytes), PATCH_ARTIFACT_SHA256)
})

test('vendor patch verify 命令只接受已批准补丁摘要', () => {
  const result = spawnSync(
    process.execPath,
    ['scripts/apply_pi_response_model_patch.mjs', '--verify'],
    {
      cwd: process.cwd(),
      encoding: 'utf8',
      env: {},
    },
  )
  assert.equal(result.status, 0)
  const output = JSON.parse(result.stdout.trim()) as {
    event: string
    patchId: string
    outcome: string
    patchedSourceSha256: string
  }
  assert.deepEqual(output, {
    event: 'country_outage_pi_response_model_vendor_patch',
    patchId: 'pi-ai-openai-completions-response-model-v1',
    outcome: 'verified',
    patchedSourceSha256: PATCHED_SHA256,
  })
  assert.equal(result.stderr, '')
})
