import { mkdtempSync, rmSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { P2S1W5CompositionRuntime } from '../src/chat/p2-s1-composition-runtime.js'
import { P2S1W5IntegratedAnswerRuntime } from '../src/chat/p2-s1-integrated-answer-runtime.js'
import { createP2S1W5HttpHandler } from '../src/server/p2-s1-w5-http-handler.js'

// 由 Python E2E 作为独立进程执行；node:test 正常测试加载时不启动服务器。
if (process.env.COUNTRY_OUTAGE_P2_S1_W5_PYTHON_E2E_SERVER === '1') {
  const token = process.env.COUNTRY_OUTAGE_P2_S1_W5_SHARED_TOKEN ?? ''
  const root = mkdtempSync(join(tmpdir(), 'w5-integrated-python-e2e-'))
  const runtime = { run: async (): Promise<never> => { throw new Error('/runs disabled') } } as unknown as P2S1W5CompositionRuntime
  const server = createServer(createP2S1W5HttpHandler({
    runtime, sharedToken: token, integratedAnswerRuntimeEnabled: true,
    integratedAnswerRuntime: new P2S1W5IntegratedAnswerRuntime(join(root, 'integrated')),
  }))
  const stop = (): void => {
    server.close(() => { rmSync(root, { recursive: true, force: true }); process.exit(0) })
  }
  process.once('SIGTERM', stop)
  process.once('SIGINT', stop)
  server.listen(0, '127.0.0.1', () => {
    const address = server.address()
    if (!address || typeof address !== 'object') throw new Error('test server address unavailable')
    process.stdout.write(`${JSON.stringify({ port: address.port, token })}\n`)
  })
}
