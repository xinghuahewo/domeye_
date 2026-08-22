import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'
import test from 'node:test'

test('冻结的 P2-S1 W5 npm 入口稳定地失败关闭', () => {
  const result = spawnSync(
    process.execPath,
    [
      resolve(
        process.cwd(),
        'dist/src/cli/serve-formal-p2-s1-w5.js',
      ),
    ],
    { encoding: 'utf8' },
  )

  assert.equal(result.status, 78)
  assert.equal(result.stdout, '')
  assert.match(
    result.stderr,
    /country_outage_agent_p2_s1_w5_retired/,
  )
  assert.match(result.stderr, /start:interactive-agent/)
})
