import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import type { AddressInfo } from 'node:net'

import type { CountryOutageReportDocument } from '../src/report/contracts.js'
import {
  CountryOutageAgentOrchestrator,
  DisabledAnnexComposer,
  DisabledExternalEvidenceProvider,
} from '../src/application/index.js'
import {
  countryOutageScopeAllowsEvent,
  createCountryOutageInternalAuthenticator,
} from '../src/cli/sidecar-security.js'
import {
  CountryOutageSessionManager,
  createCountryOutageAgentHttpHandler,
  type CountryOutageReportServiceIdentity,
} from '../src/server/index.js'

const REFERENCE = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const PUBLICATION = 'publication_http_test'

function reportDocument(): CountryOutageReportDocument {
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: 'report_http_test',
    reportContentSha256: 'd'.repeat(64),
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'd'.repeat(64),
    generatedAt: '2026-07-28T10:00:00.000Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: 'incident-http',
      legacy_reference: REFERENCE,
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗',
    },
    snapshot: {
      incidentId: 'incident-http',
      publicationId: PUBLICATION,
      revision: 1,
      dataThrough: '2026-02-28T15:00:00Z',
      isFinal: true,
      cohortId: 'cohort-http',
      collectorId: 'rrc25',
      windowStartUtc: '2026-02-28T10:05:00Z',
      windowEndUtc: '2026-02-28T15:00:00Z',
    },
    factSetId: 'facts_http',
    model: {
      provider: 'test',
      model: 'test',
      modelVersion: '1',
      adapter: 'deterministic-acceptance',
    },
    validation: {
      passed: true,
      errors: [],
      warnings: [],
      checkedEvidenceRefs: ['overview:/observation_scope'],
    },
    draft: {
      schemaVersion: 'country_outage_report_draft_v1',
      title: '伊朗 BGP 路由可见性观测报告',
      subtitle: 'HTTP 测试',
      summary: {
        text: '仅描述 RRC25 控制面。',
        evidenceRefs: ['overview:/observation_scope'],
      },
      highlights: [
        {
          label: '观测源',
          value: 'RRC25',
          evidenceRefs: ['overview:/observation_scope'],
        },
      ],
      sections: [
        {
          id: 'scope',
          title: '观测范围',
          paragraphs: [
            {
              text: '仅描述 RRC25 控制面。',
              evidenceRefs: ['overview:/observation_scope'],
            },
          ],
        },
      ],
      unknowns: [
        '全国数据面状态',
        '用户与业务影响',
        '原因与责任',
        '窗口之后是否完全恢复',
      ],
    },
  }
}

async function withServer(
  run: (
    baseUrl: string,
    manager: CountryOutageSessionManager,
  ) => Promise<void>,
  now: () => number = Date.now,
): Promise<void> {
  const document = reportDocument()
  const markdown = Buffer.from('# HTTP 下载测试', 'utf8')
  const pdf = Buffer.from('%PDF-1.7 test', 'utf8')
  const manager = new CountryOutageSessionManager({
    reportService: {
      async generate(input) {
        input.onPhase('generating_report')
        input.onPhase('validating')
        return {
          document,
          artifacts: {
            artifactId: document.artifactId,
            markdown: {
              status: 'ready',
              artifact: {
                format: 'markdown',
                filename: 'IR_http.md',
                mediaType: 'text/markdown; charset=utf-8',
                byteLength: markdown.byteLength,
                sha256: 'e'.repeat(64),
                content: markdown,
              },
            },
            pdf: {
              status: 'ready',
              artifact: {
                format: 'pdf',
                filename: 'IR_http.pdf',
                mediaType: 'application/pdf',
                byteLength: pdf.byteLength,
                sha256: 'f'.repeat(64),
                content: pdf,
              },
            },
          },
        }
      },
    },
    questionService: {
      async answer() {
        return {
          kind: 'evidence_boundary',
          text: '只根据原报告回答。',
          evidenceRefs: ['overview:/observation_scope'],
          evidenceRecords: [
            {
              evidenceRef: 'overview:/observation_scope',
              source: 'overview',
              label: '观测范围',
              metric: null,
              value: 'RRC25',
              observedAtUtc: null,
              observedAtLocal: null,
              statisticalScope: 'RRC25 固定统计范围',
            },
          ],
          missingEvidence: ['用户影响证据'],
          limitations: [],
        }
      },
    },
    authorize: (principal) => principal.userId === 'http-user',
    now,
    timersEnabled: false,
  })
  const application = new CountryOutageAgentOrchestrator({
    core: manager,
    externalEvidenceProvider: new DisabledExternalEvidenceProvider(
      () => new Date(now()),
    ),
    annexComposer: new DisabledAnnexComposer(),
  })
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application,
      authenticate: (request) =>
        request.headers.authorization === 'Bearer allowed'
          ? {
              userId: 'http-user',
              authorizationScope: 'scope-http',
            }
          : null,
      sseHeartbeatMs: 60_000,
    }),
  )
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo
  try {
    await run(`http://127.0.0.1:${address.port}`, manager)
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
}

test('HTTP 窄接口、SSE 重放和授权下载可直接挂接 node:http', async () => {
  await withServer(async (baseUrl) => {
    const unauthorized = await fetch(`${baseUrl}/country-outage/reports`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    assert.equal(unauthorized.status, 401)

    const createdResponse = await fetch(
      `${baseUrl}/country-outage/reports`,
      {
        method: 'POST',
        headers: {
          Authorization: 'Bearer allowed',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'http-report-0001',
        }),
      },
    )
    assert.equal(createdResponse.status, 202)
    const created = (await createdResponse.json()) as {
      report_id: string
      run_id: string
      session: { expires_at: string }
    }
    assert.match(created.report_id, /^cor_/)
    assert.match(created.run_id, /^run_/)
    assert.ok(created.session.expires_at)

    await new Promise<void>((resolve) => setImmediate(resolve))
    const sseController = new AbortController()
    const sse = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/events`,
      {
        headers: {
          Authorization: 'Bearer allowed',
          'Last-Event-ID': '2',
        },
        signal: sseController.signal,
      },
    )
    assert.equal(sse.status, 200)
    assert.match(
      sse.headers.get('content-type') ?? '',
      /text\/event-stream/,
    )
    const reader = sse.body!.getReader()
    const decoder = new TextDecoder()
    let sseText = ''
    for (let index = 0; index < 100; index += 1) {
      const chunk = await Promise.race([
        reader.read(),
        new Promise<never>((_resolve, reject) => {
          const timeout = setTimeout(
            () => reject(new Error('等待 SSE 完成事件超时')),
            2_000,
          )
          timeout.unref()
        }),
      ])
      if (chunk.done) break
      sseText += decoder.decode(chunk.value, { stream: true })
      if (sseText.includes('"phase":"completed"')) break
    }
    sseController.abort()
    assert.doesNotMatch(sseText, /id: 1\n/)
    assert.doesNotMatch(sseText, /id: 2\n/)
    assert.match(sseText, /event: report_state/)
    assert.match(sseText, /"phase":"completed"/)
    assert.match(sseText, /"artifactId":"report_http_test"/)

    const download = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/artifacts/markdown`,
      { headers: { Authorization: 'Bearer allowed' } },
    )
    assert.equal(download.status, 200)
    assert.equal(download.headers.get('x-artifact-id'), 'report_http_test')
    assert.equal(download.headers.get('x-content-sha256'), 'e'.repeat(64))
    assert.match(await download.text(), /HTTP 下载测试/)

    const question = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/questions`,
      {
        method: 'POST',
        headers: {
          Authorization: 'Bearer allowed',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          question: '这能说明用户影响吗？',
          evidence_mode: 'domeye_only',
          quote: {
            kind: 'section_paragraph',
            section_id: 'scope',
            paragraph_index: 0,
            evidence_refs: ['overview:/observation_scope'],
          },
          idempotency_key: 'http-question-0001',
        }),
      },
    )
    assert.equal(question.status, 202)
    const questionBody = (await question.json()) as {
      question_id: string
      run_id: string
    }
    assert.match(questionBody.question_id, /^q_/)
    const ordinaryAppendix = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/questions/${questionBody.question_id}/artifacts/external-appendix`,
      { headers: { Authorization: 'Bearer allowed' } },
    )
    assert.equal(ordinaryAppendix.status, 409)
    assert.equal(
      ((await ordinaryAppendix.json()) as {
        error: { code: string }
      }).error.code,
      'external_evidence_not_configured',
    )

    const aborted = await fetch(
      `${baseUrl}/country-outage/runs/${questionBody.run_id}/abort`,
      {
        method: 'POST',
        headers: {
          Authorization: 'Bearer allowed',
          'Content-Type': 'application/json',
        },
        body: '{}',
      },
    )
    assert.equal(aborted.status, 200)
  })
})

test('HTTP 会话到期不截断已开始下载，但拒绝到期后新下载', async () => {
  let now = Date.now()
  await withServer(async (baseUrl, manager) => {
    const createdResponse = await fetch(
      `${baseUrl}/country-outage/reports`,
      {
        method: 'POST',
        headers: {
          Authorization: 'Bearer allowed',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'expiry-download-0001',
        }),
      },
    )
    assert.equal(createdResponse.status, 202)
    const created = (await createdResponse.json()) as {
      report_id: string
    }
    await new Promise<void>((resolve) => setImmediate(resolve))

    const started = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/artifacts/markdown`,
      { headers: { Authorization: 'Bearer allowed' } },
    )
    assert.equal(started.status, 200)

    now += 30 * 60 * 1000
    manager.sweep()
    assert.match(await started.text(), /HTTP 下载测试/)

    const afterExpiry = await fetch(
      `${baseUrl}/country-outage/reports/${created.report_id}/artifacts/markdown`,
      { headers: { Authorization: 'Bearer allowed' } },
    )
    assert.equal(afterExpiry.status, 410)
    const error = (await afterExpiry.json()) as {
      error: { code: string }
    }
    assert.equal(error.error.code, 'session_expired')

  }, () => now)
})

test('HTTP 双用户仅复用同权限基础报告，问答、SSE、下载和运行保持隔离，无事件权限失败关闭', async () => {
  const document = reportDocument()
  const markdown = Buffer.from('# 双用户 HTTP 隔离测试', 'utf8')
  const pdf = Buffer.from('%PDF-1.7 dual-user-test', 'utf8')
  let generationCalls = 0
  const reportServiceIdentity: CountryOutageReportServiceIdentity = {
    reportSpecificationVersion: document.reportSpecificationVersion,
    projectKnowledgeVersion: document.projectKnowledgeVersion,
    validatorRulesVersion: document.validatorRulesVersion,
    skillBundleSha256: document.skillBundleSha256,
    model: document.model,
  }
  const manager = new CountryOutageSessionManager({
    reportService: {
      async generate(input) {
        generationCalls += 1
        input.onPhase('generating_report')
        input.onPhase('validating')
        return {
          document,
          artifacts: {
            artifactId: document.artifactId,
            markdown: {
              status: 'ready' as const,
              artifact: {
                format: 'markdown' as const,
                filename: 'IR_dual_user.md',
                mediaType: 'text/markdown; charset=utf-8',
                byteLength: markdown.byteLength,
                sha256: 'a'.repeat(64),
                content: markdown,
              },
            },
            pdf: {
              status: 'ready' as const,
              artifact: {
                format: 'pdf' as const,
                filename: 'IR_dual_user.pdf',
                mediaType: 'application/pdf',
                byteLength: pdf.byteLength,
                sha256: 'b'.repeat(64),
                content: pdf,
              },
            },
          },
        }
      },
    },
    questionService: {
      async answer(input) {
        return {
          kind: 'evidence_boundary',
          text: `只回答会话 ${input.reportId} 的 RRC25 事实。`,
          evidenceRefs: ['overview:/observation_scope'],
          evidenceRecords: [
            {
              evidenceRef: 'overview:/observation_scope',
              source: 'overview',
              label: '观测范围',
              metric: null,
              value: 'RRC25',
              observedAtUtc: null,
              observedAtLocal: null,
              statisticalScope: 'RRC25 固定统计范围',
            },
          ],
          missingEvidence: [],
          limitations: [],
        }
      },
    },
    baseReportCache: { reportServiceIdentity },
    authorize: (principal, reference) =>
      reference.replace(' ', '+') === REFERENCE.replace(' ', '+') &&
      countryOutageScopeAllowsEvent(principal, reference),
    timersEnabled: false,
  })
  const application = new CountryOutageAgentOrchestrator({
    core: manager,
    externalEvidenceProvider: new DisabledExternalEvidenceProvider(),
    annexComposer: new DisabledAnnexComposer(),
  })
  const sharedToken = 'dual-user-sidecar-token-0001'
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application,
      authenticate: createCountryOutageInternalAuthenticator(sharedToken),
      sseHeartbeatMs: 60_000,
    }),
  )
  await new Promise<void>((resolve) =>
    server.listen(0, '127.0.0.1', resolve),
  )
  const address = server.address() as AddressInfo
  const baseUrl = `http://127.0.0.1:${address.port}`
  const headers = (
    userId: string,
    authorizationScope: string,
    json = false,
  ): Record<string, string> => ({
    Authorization: `Bearer ${sharedToken}`,
    'X-Domeye-User': userId,
    'X-Domeye-Authorization-Scope': authorizationScope,
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  })
  const createReport = async (
    userId: string,
    authorizationScope: string,
    idempotencyKey: string,
  ): Promise<{
    response: Response
    body: {
      report_id?: string
      run_id?: string
      error?: { code: string }
    }
  }> => {
    const response = await fetch(`${baseUrl}/country-outage/reports`, {
      method: 'POST',
      headers: headers(userId, authorizationScope, true),
      body: JSON.stringify({
        event_reference: REFERENCE,
        publication_id: PUBLICATION,
        revision: 1,
        idempotency_key: idempotencyKey,
      }),
    })
    return {
      response,
      body: (await response.json()) as {
        report_id?: string
        run_id?: string
        error?: { code: string }
      },
    }
  }
  const errorCode = async (response: Response): Promise<string> =>
    ((await response.json()) as { error: { code: string } }).error.code
  const waitForAsyncRuns = async (): Promise<void> => {
    await new Promise<void>((resolve) => setImmediate(resolve))
    await new Promise<void>((resolve) => setImmediate(resolve))
  }
  const readSseUntil = async (
    reportId: string,
    requestHeaders: Record<string, string>,
    expectedQuestionId: string,
  ): Promise<string> => {
    const controller = new AbortController()
    const response = await fetch(
      `${baseUrl}/country-outage/reports/${reportId}/events`,
      { headers: requestHeaders, signal: controller.signal },
    )
    assert.equal(response.status, 200)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let text = ''
    try {
      for (let index = 0; index < 100; index += 1) {
        const chunk = await Promise.race([
          reader.read(),
          new Promise<never>((_resolve, reject) => {
            const timeout = setTimeout(
              () => reject(new Error('等待双用户 SSE 重放超时')),
              2_000,
            )
            timeout.unref()
          }),
        ])
        if (chunk.done) break
        text += decoder.decode(chunk.value, { stream: true })
        if (
          text.includes(`"question_id":"${expectedQuestionId}"`) &&
          text.includes('"phase":"completed"')
        ) {
          break
        }
      }
    } finally {
      controller.abort()
      await reader.cancel().catch(() => undefined)
    }
    return text
  }

  try {
    const userA = await createReport(
      'dual-user-a',
      'country_outage_event_read:IR',
      'dual-user-report-a',
    )
    assert.equal(userA.response.status, 202)
    await waitForAsyncRuns()
    const userB = await createReport(
      'dual-user-b',
      'country_outage_event_read:IR',
      'dual-user-report-b',
    )
    assert.equal(userB.response.status, 202)
    await waitForAsyncRuns()
    assert.ok(userA.body.report_id)
    assert.ok(userB.body.report_id)
    assert.ok(userA.body.run_id)
    assert.notEqual(userA.body.report_id, userB.body.report_id)
    assert.notEqual(userA.body.run_id, userB.body.run_id)
    assert.equal(generationCalls, 1)

    const globalScopeUser = await createReport(
      'dual-user-global',
      'country_outage_event_read',
      'dual-user-report-global',
    )
    assert.equal(globalScopeUser.response.status, 202)
    await waitForAsyncRuns()
    assert.equal(generationCalls, 2)

    const noIrAccess = await createReport(
      'dual-user-no-ir',
      'country_outage_event_read:CN',
      'dual-user-report-denied',
    )
    assert.equal(noIrAccess.response.status, 403)
    assert.equal(noIrAccess.body.error?.code, 'event_access_denied')
    assert.equal(generationCalls, 2)

    const forgedWithoutToken = await fetch(
      `${baseUrl}/country-outage/reports`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Domeye-User': 'browser-forged-user',
          'X-Domeye-Authorization-Scope': 'country_outage_event_read',
        },
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'dual-user-forged-browser',
        }),
      },
    )
    assert.equal(forgedWithoutToken.status, 401)
    assert.equal(
      await errorCode(forgedWithoutToken),
      'authentication_required',
    )

    const userAReportId = userA.body.report_id!
    const userBReportId = userB.body.report_id!
    const userAHeaders = headers(
      'dual-user-a',
      'country_outage_event_read:IR',
    )
    const userBHeaders = headers(
      'dual-user-b',
      'country_outage_event_read:IR',
    )

    const crossUserDownload = await fetch(
      `${baseUrl}/country-outage/reports/${userAReportId}/artifacts/markdown`,
      { headers: userBHeaders },
    )
    assert.equal(crossUserDownload.status, 404)
    assert.equal(
      await errorCode(crossUserDownload),
      'report_not_found',
    )
    const crossUserSse = await fetch(
      `${baseUrl}/country-outage/reports/${userAReportId}/events`,
      { headers: userBHeaders },
    )
    assert.equal(crossUserSse.status, 404)
    assert.equal(await errorCode(crossUserSse), 'report_not_found')
    const crossUserQuestion = await fetch(
      `${baseUrl}/country-outage/reports/${userAReportId}/questions`,
      {
        method: 'POST',
        headers: headers(
          'dual-user-b',
          'country_outage_event_read:IR',
          true,
        ),
        body: JSON.stringify({
          question: '尝试读取另一用户报告',
          evidence_mode: 'domeye_only',
          idempotency_key: 'dual-user-cross-question',
        }),
      },
    )
    assert.equal(crossUserQuestion.status, 404)
    assert.equal(
      await errorCode(crossUserQuestion),
      'report_not_found',
    )
    const crossUserAbort = await fetch(
      `${baseUrl}/country-outage/runs/${userA.body.run_id}/abort`,
      {
        method: 'POST',
        headers: headers(
          'dual-user-b',
          'country_outage_event_read:IR',
          true,
        ),
        body: '{}',
      },
    )
    assert.equal(crossUserAbort.status, 404)
    assert.equal(await errorCode(crossUserAbort), 'report_not_found')

    const ownDownloadA = await fetch(
      `${baseUrl}/country-outage/reports/${userAReportId}/artifacts/markdown`,
      { headers: userAHeaders },
    )
    const ownDownloadB = await fetch(
      `${baseUrl}/country-outage/reports/${userBReportId}/artifacts/markdown`,
      { headers: userBHeaders },
    )
    assert.equal(ownDownloadA.status, 200)
    assert.equal(ownDownloadB.status, 200)
    assert.equal(await ownDownloadA.text(), await ownDownloadB.text())

    const ownQuestion = async (
      userId: string,
      reportId: string,
      idempotencyKey: string,
    ): Promise<{ question_id: string; run_id: string }> => {
      const response = await fetch(
        `${baseUrl}/country-outage/reports/${reportId}/questions`,
        {
          method: 'POST',
          headers: headers(
            userId,
            'country_outage_event_read:IR',
            true,
          ),
          body: JSON.stringify({
            question: '该报告能说明用户影响吗？',
            evidence_mode: 'domeye_only',
            idempotency_key: idempotencyKey,
          }),
        },
      )
      assert.equal(response.status, 202)
      return (await response.json()) as {
        question_id: string
        run_id: string
      }
    }
    const questionA = await ownQuestion(
      'dual-user-a',
      userAReportId,
      'dual-user-question-a',
    )
    await waitForAsyncRuns()
    const questionB = await ownQuestion(
      'dual-user-b',
      userBReportId,
      'dual-user-question-b',
    )
    await waitForAsyncRuns()
    assert.notEqual(questionA.question_id, questionB.question_id)
    assert.notEqual(questionA.run_id, questionB.run_id)

    const sseA = await readSseUntil(
      userAReportId,
      userAHeaders,
      questionA.question_id,
    )
    const sseB = await readSseUntil(
      userBReportId,
      userBHeaders,
      questionB.question_id,
    )
    assert.match(sseA, new RegExp(questionA.question_id))
    assert.doesNotMatch(sseA, new RegExp(questionB.question_id))
    assert.match(sseB, new RegExp(questionB.question_id))
    assert.doesNotMatch(sseB, new RegExp(questionA.question_id))
    assert.doesNotMatch(sseA, new RegExp(userBReportId))
    assert.doesNotMatch(sseB, new RegExp(userAReportId))

    const changedScopeDownload = await fetch(
      `${baseUrl}/country-outage/reports/${userAReportId}/artifacts/markdown`,
      {
        headers: headers(
          'dual-user-a',
          'country_outage_event_read:CN',
        ),
      },
    )
    assert.equal(changedScopeDownload.status, 403)
    assert.equal(
      await errorCode(changedScopeDownload),
      'authorization_scope_changed',
    )
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
})

test('HTTP 拒绝未允许字段和请求体/Header 幂等键冲突', async () => {
  await withServer(async (baseUrl) => {
    const invalid = await fetch(`${baseUrl}/country-outage/reports`, {
      method: 'POST',
      headers: {
        Authorization: 'Bearer allowed',
        'Content-Type': 'application/json',
        'Idempotency-Key': 'header-key-0001',
      },
      body: JSON.stringify({
        event_reference: REFERENCE,
        publication_id: PUBLICATION,
        revision: 1,
        idempotency_key: 'body-key-000001',
        url: 'https://example.invalid',
      }),
    })
    assert.equal(invalid.status, 400)
    const body = (await invalid.json()) as {
      error: { code: string }
    }
    assert.equal(body.error.code, 'invalid_request_fields')

    const conflict = await fetch(`${baseUrl}/country-outage/reports`, {
      method: 'POST',
      headers: {
        Authorization: 'Bearer allowed',
        'Content-Type': 'application/json',
        'Idempotency-Key': 'header-key-0001',
      },
      body: JSON.stringify({
        event_reference: REFERENCE,
        publication_id: PUBLICATION,
        revision: 1,
        idempotency_key: 'body-key-000001',
      }),
    })
    assert.equal(conflict.status, 409)
    const conflictBody = (await conflict.json()) as {
      error: { code: string }
    }
    assert.equal(conflictBody.error.code, 'idempotency_conflict')
  })
})
