import assert from 'node:assert/strict'
import {
  chmod,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

import type { CountryOutageReportDocument } from '../src/report/contracts.js'
import {
  COUNTRY_OUTAGE_PDF_MAX_BYTES,
  CountryOutagePdfRenderer,
  PdfRenderAbortedError,
  PdfRenderProcessError,
  PdfRenderSizeLimitError,
  PdfRenderTimeoutError,
} from '../src/report/pdf-renderer.js'
import { MUTABLE_MODEL_ALIAS_LIMITATION_ZH } from '../src/pi/index.js'

const bundledPython =
  '/Users/botongwu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
const arialUnicode =
  '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
const canRunIntegration =
  existsSync(bundledPython) && existsSync(arialUnicode)

function reportDocument(): CountryOutageReportDocument {
  const evidence = (
    text: string,
    ...evidenceRefs: string[]
  ): { text: string; evidenceRefs: string[] } => ({
    text,
    evidenceRefs,
  })
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: 'report_a2_pdf_acceptance_0123456789abcdef',
    reportContentSha256:
      '6de26fe9c9c2b89fc2a396a9f9b4f6ee1a5a7e1a47922f8c6fead149d7fd5b98',
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'd'.repeat(64),
    generatedAt: '2026-07-28T08:30:00.000Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由观测',
    },
    snapshot: {
      incidentId: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publicationId: 'publication-ir-rrc25-revision-1',
      revision: 1,
      dataThrough: '2026-02-28T15:00:00Z',
      isFinal: true,
      cohortId: 'cohort-ir-fixed-v1',
      collectorId: 'rrc25',
      windowStartUtc: '2026-02-28T10:05:00Z',
      windowEndUtc: '2026-02-28T15:00:00Z',
    },
    factSetId: 'facts_ir_rrc25_revision_1_0123456789abcdef',
    model: {
      provider: 'acceptance',
      model: 'deterministic-narrator',
      modelVersion: 'a2-pdf-fixture-v1',
      adapter: 'deterministic-acceptance',
    },
    validation: {
      passed: true,
      errors: [],
      warnings: ['缺少可信长期正常基线。'],
      checkedEvidenceRefs: [
        'fact:visibility:start',
        'fact:visibility:lowest',
        'fact:visibility:end',
      ],
    },
    draft: {
      schemaVersion: 'country_outage_report_draft_v1',
      title: '伊朗 BGP 路由可见性观测报告',
      subtitle: '近五小时内路由可见性明显下降，窗口结束时仍未回到起点水平',
      summary: evidence(
        '2026年2月28日18:05至23:00，Domeye通过RRC25对伊朗相关BGP路由进行了连续观测。'
          + '固定统计范围内的路由可见性显著下降，窗口后段虽有回升，但结束时仍明显低于起点。',
        'fact:visibility:start',
        'fact:visibility:lowest',
        'fact:visibility:end',
      ),
      highlights: [
        {
          label: '固定观测范围',
          value: '563个origin ASN',
          evidenceRefs: ['overview:/cohort/origin_asn_count'],
        },
        {
          label: '固定路由观测关系',
          value: '384,767条',
          evidenceRefs: ['overview:/cohort/prefix_vp_count'],
        },
        {
          label: '窗口起点覆盖率',
          value: '95.44%',
          evidenceRefs: ['fact:visibility:start'],
        },
        {
          label: '窗口最低覆盖率',
          value: '82.32%',
          evidenceRefs: ['fact:visibility:lowest'],
        },
        {
          label: '窗口结束覆盖率',
          value: '86.79%',
          evidenceRefs: ['fact:visibility:end'],
        },
        {
          label: '最大单槽下降',
          value: '5分钟内减少35,806条',
          evidenceRefs: ['fact:visibility:largest_drop'],
        },
        {
          label: '全不可见ASN峰值',
          value: '87个',
          evidenceRefs: ['series:/metric_extrema/fully_invisible_asn_count/max'],
        },
        {
          label: 'UPDATE活动峰值',
          value: '340,960条/5分钟',
          evidenceRefs: ['series:/metric_extrema/update_total/max'],
        },
      ],
      sections: [
        {
          id: 'scope',
          title: '观测范围与阅读边界',
          paragraphs: [
            evidence(
              '本报告使用唯一观测源RRC25和固定统计口径。'
                + '“路由观测关系”表示某个前缀是否能从某个BGP观测点看到，'
                + '它不是唯一前缀数，也不能直接换算为用户数。',
              'overview:/observation_scope',
              'overview:/cohort',
            ),
            evidence(
              '这些数据属于BGP控制面观测，不能直接回答伊朗用户能否上网、'
                + '具体业务是否中断，也不能仅凭本快照认定事件原因。',
              'audit:/evidence_level',
            ),
          ],
        },
        {
          id: 'key_numbers',
          title: '可见性是怎样下降的',
          paragraphs: [
            evidence(
              '18:05观察开始时，384,767条固定路由观测关系中有367,215条可见，'
                + '覆盖率为95.44%。22:35降至窗口最低点316,733条，覆盖率82.32%。',
              'fact:visibility:start',
              'fact:visibility:lowest',
            ),
            evidence(
              '与窗口起点相比，最低点减少50,482条，相当于起点可见关系的13.75%。'
                + '这是确定性计算结果，不是模型估算。',
              'derived:start_to_lowest_visible_prefix_vp_change',
              'derived:start_to_lowest_visible_prefix_vp_change_ratio',
            ),
          ],
        },
        {
          id: 'visibility',
          title: '影响扩展到了多少网络',
          paragraphs: [
            evidence(
              '页面持续观察563个伊朗origin ASN。窗口内部分可见ASN峰值为188个，'
                + '全不可见ASN峰值为87个；两个峰值发生在不同时间，不能简单相加。',
              'overview:/cohort/origin_asn_count',
              'series:/metric_extrema/partially_visible_asn_count/max',
              'series:/metric_extrema/fully_invisible_asn_count/max',
            ),
          ],
        },
        {
          id: 'asn_scope',
          title: '持续全不可见的ASN',
          paragraphs: [
            evidence(
              '部分ASN的不可见状态持续接近整个观察窗口。持续时间反映控制面状态，'
                + '不同ASN的路由规模差异很大，因此不能把持续时间排名理解为实际影响排名。',
              'asn_page:1:/items',
            ),
          ],
        },
        {
          id: 'address_families',
          title: 'IPv4受到的变化更明显',
          paragraphs: [
            evidence(
              'IPv4最低覆盖率为82.285%，IPv6最低覆盖率为95.327%。'
                + '这说明本窗口内变化主要体现在IPv4，但不表示IPv4用户受到同比例影响。',
              'series:/metric_extrema/ipv4_visible_prefix_vp_ratio/min',
              'series:/metric_extrema/ipv6_visible_prefix_vp_ratio/min',
            ),
          ],
        },
        {
          id: 'updates',
          title: '大规模BGP更新活动出现在下降之前',
          paragraphs: [
            evidence(
              '18:25 UPDATE总量达到340,960条，五分钟后出现窗口内最大单槽下降。'
                + '两者在时间上紧密相邻，但现有页面证据不足以证明因果关系。',
              'series:/metric_extrema/update_total/max',
              'fact:visibility:largest_drop',
            ),
          ],
        },
        {
          id: 'end_state',
          title: '窗口后段出现回升，但还不能称为恢复',
          paragraphs: [
            evidence(
              '22:40后出现部分回升。到23:00覆盖率达到86.79%，'
                + '但仍比窗口起点少33,277条可见关系，不能据此确认事件已经结束。',
              'fact:visibility:largest_recovery',
              'fact:visibility:end',
              'derived:end_gap_from_start',
            ),
          ],
        },
        {
          id: 'resources',
          title: '国家级路由资源也出现波动',
          paragraphs: [
            evidence(
              'IPv4 /24等价资源从窗口最大39,260个下降到最低37,379个。'
                + '这些数字是规范化、去重后的路由资源覆盖，不是实际在线IP地址。',
              'series:/resource_metric_extrema/ipv4_24_equivalent_count',
            ),
          ],
        },
        {
          id: 'assessment',
          title: '综合判断与结论',
          paragraphs: [
            evidence(
              'RRC25固定观测范围内，伊朗相关BGP路由出现明显、广泛且持续的可见性下降。'
                + '窗口后段虽有部分回升，截至23:00仍未回到起点水平。',
              'fact:visibility:start',
              'fact:visibility:lowest',
              'fact:visibility:end',
            ),
          ],
        },
      ],
      unknowns: [
        '是否属于全国性互联网中断。',
        '用户和具体业务受到多大影响。',
        '事件由攻击、配置错误、政策行为还是基础设施故障引起。',
        '哪个运营商或ASN应承担责任。',
        '窗口结束之后是否已经完全恢复。',
      ],
    },
  }
}

function mutableAliasReportDocument(): CountryOutageReportDocument {
  const document = reportDocument()
  document.model = {
    ...document.model,
    adapter: 'pi-sdk',
    piVersion: '0.82.1',
    runtimeIdentity: 'formal',
    modelRevisionKind: 'mutable_alias',
    immutableRevisionAvailable: false,
    limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
    certificationValidUntil: '2026-08-12T16:00:00Z',
    certifiedScenarioSetId:
      'country-outage-certified-scenarios-v1',
    certifiedInputScope: 'legal_country_outage_rrc25_v1',
  }
  return document
}

async function fakeExecutable(
  source: string,
): Promise<{ directory: string; executable: string }> {
  const directory = await mkdtemp(join(tmpdir(), 'domeye-pdf-test-'))
  const executable = join(directory, 'fake-python')
  await writeFile(executable, `#!/usr/bin/env node\n${source}\n`, 'utf8')
  await chmod(executable, 0o755)
  return { directory, executable }
}

test(
  '使用受信任的 Python 和中文字体从 ReportDocument 生成可提取文本的 PDF',
  { skip: !canRunIntegration },
  async () => {
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: bundledPython,
      fontPath: arialUnicode,
      timeoutMs: 20_000,
    })
    const pdf = await renderer.render(reportDocument())
    const repeatedPdf = await renderer.render(reportDocument())
    assert.equal(pdf.subarray(0, 5).toString('ascii'), '%PDF-')
    assert.deepEqual(repeatedPdf, pdf)
    assert.ok(pdf.byteLength > 10_000)
    assert.ok(pdf.byteLength <= COUNTRY_OUTAGE_PDF_MAX_BYTES)

    const inspection = spawnSync(
      bundledPython,
      [
        '-c',
        [
          'import io,json,pdfplumber,sys',
          'pdf=pdfplumber.open(io.BytesIO(sys.stdin.buffer.read()))',
          'pages=[(page.extract_text() or "") for page in pdf.pages]',
          'print(json.dumps({"count":len(pages),"lengths":[len(x) for x in pages],"text":"\\n".join(pages)},ensure_ascii=False))',
        ].join(';'),
      ],
      { input: pdf, maxBuffer: 2 * 1024 * 1024 },
    )
    assert.equal(inspection.status, 0, inspection.stderr.toString('utf8'))
    const result = JSON.parse(inspection.stdout.toString('utf8')) as {
      count: number
      lengths: number[]
      text: string
    }
    assert.ok(result.count >= 2)
    assert.ok(result.lengths.every((length) => length > 80))
    assert.match(result.text, /伊朗 BGP 路由可见性观测报告/)
    assert.match(result.text, /最值得关注的数字/)
    assert.match(result.text, /不能仅凭本报告回答的问题/)
    assert.match(result.text, /制品与证据说明/)
    assert.match(result.text, /第 1 页/)
    assert.equal(result.text.match(/最值得关注的数字/g)?.length, 1)

    const acceptanceOutput = process.env.DOMEYE_PDF_ACCEPTANCE_OUTPUT
    if (acceptanceOutput) {
      await writeFile(acceptanceOutput, pdf)
    }
  },
)

test(
  '未通过机器校验的文档不会产生 PDF',
  { skip: !canRunIntegration },
  async () => {
    const invalid = reportDocument()
    invalid.validation.passed = false
    invalid.validation.errors = ['验收构造的失败结果']
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: bundledPython,
      fontPath: arialUnicode,
      timeoutMs: 20_000,
    })
    await assert.rejects(
      renderer.render(invalid),
      (error: unknown) =>
        error instanceof PdfRenderProcessError
        && /passed machine validation/.test(error.message),
    )
  },
)

test(
  'PDF 条件展示正式 Pi 可变别名限制、有效期与认证输入范围',
  { skip: !canRunIntegration },
  async () => {
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: bundledPython,
      fontPath: arialUnicode,
      timeoutMs: 20_000,
    })
    const pdf = await renderer.render(mutableAliasReportDocument())
    const inspection = spawnSync(
      bundledPython,
      [
        '-c',
        [
          'import io,pdfplumber,sys',
          'pdf=pdfplumber.open(io.BytesIO(sys.stdin.buffer.read()))',
          'print("\\n".join((page.extract_text() or "") for page in pdf.pages))',
        ].join(';'),
      ],
      { input: pdf, maxBuffer: 2 * 1024 * 1024 },
    )
    assert.equal(
      inspection.status,
      0,
      inspection.stderr.toString('utf8'),
    )
    const text = inspection.stdout.toString('utf8')
    assert.match(text, /模型引用类型 可变别名（mutable_alias）/u)
    assert.match(text, /不可变权重 revision 供应方未提供/u)
    assert.match(text, /供应方未提供不可变权重 revision/u)
    assert.match(text, /认证有效至 2026-08-12T16:00:00Z/u)
    assert.match(
      text,
      /认证场景集 country-outage-certified-scenarios-v1/u,
    )
    assert.match(
      text,
      /认证输入范围 legal_country_outage_rrc25_v1/u,
    )
  },
)

test(
  '超过冻结的 40 页上限时不向调用方发布 PDF',
  { skip: !canRunIntegration },
  async () => {
    const oversized = reportDocument()
    oversized.draft.sections = Array.from({ length: 55 }, (_, index) => ({
      id: 'assessment' as const,
      title: `分页上限验证章节 ${index + 1}`,
      paragraphs: [{
        text: (
          '这是一段只用于验证PDF固定分页上限的验收文本。'
          + '它不代表任何真实事件事实，也不会进入正式报告。'
        ).repeat(30),
        evidenceRefs: [`acceptance:page-limit:${index + 1}`],
      }],
    }))
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: bundledPython,
      fontPath: arialUnicode,
      timeoutMs: 20_000,
    })
    await assert.rejects(
      renderer.render(oversized),
      (error: unknown) =>
        error instanceof PdfRenderProcessError
        && /maximum is 40/.test(error.message),
    )
  },
)

test('模型侧不能改变固定脚本，子进程只收到脚本路径参数', async () => {
  const fake = await fakeExecutable(
    [
      "const fs=require('node:fs')",
      "fs.writeFileSync(process.argv[1]+'.argv', JSON.stringify(process.argv.slice(2)))",
      "process.stdout.write('%PDF-1.4\\n%%EOF\\n')",
    ].join(';'),
  )
  try {
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: fake.executable,
      fontPath: '/trusted/configured/font.ttf',
    })
    const pdf = await renderer.render(reportDocument())
    assert.match(pdf.toString('ascii'), /^%PDF-/)
    const argv = JSON.parse(
      await readFile(`${fake.executable}.argv`, 'utf8'),
    ) as string[]
    assert.equal(argv.length, 1)
    assert.match(argv[0] ?? '', /scripts\/render_country_outage_report\.py$/)
    assert.doesNotMatch(argv[0] ?? '', /伊朗|publication|artifact/)
  } finally {
    await rm(fake.directory, { recursive: true, force: true })
  }
})

test('AbortSignal 会终止正在运行的 PDF 子进程', async () => {
  const fake = await fakeExecutable(
    "process.stdin.resume();setTimeout(()=>process.stdout.write('%PDF-1.4\\n%%EOF\\n'),5000)",
  )
  try {
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: fake.executable,
      fontPath: '/trusted/configured/font.ttf',
      timeoutMs: 10_000,
    })
    const controller = new AbortController()
    const result = renderer.render(reportDocument(), controller.signal)
    setTimeout(() => controller.abort(), 25)
    await assert.rejects(result, PdfRenderAbortedError)
  } finally {
    await rm(fake.directory, { recursive: true, force: true })
  }
})

test('超过渲染时限会终止 PDF 子进程', async () => {
  const fake = await fakeExecutable(
    "process.stdin.resume();setTimeout(()=>process.stdout.write('%PDF-1.4\\n%%EOF\\n'),5000)",
  )
  try {
    const renderer = new CountryOutagePdfRenderer({
      pythonExecutable: fake.executable,
      fontPath: '/trusted/configured/font.ttf',
      timeoutMs: 30,
    })
    await assert.rejects(
      renderer.render(reportDocument()),
      PdfRenderTimeoutError,
    )
  } finally {
    await rm(fake.directory, { recursive: true, force: true })
  }
})

test('超过 10 MiB 的输入或 PDF 输出均失败关闭', async () => {
  const largeInput = reportDocument()
  largeInput.draft.title = '大'.repeat(COUNTRY_OUTAGE_PDF_MAX_BYTES)
  const inputRenderer = new CountryOutagePdfRenderer({
    pythonExecutable: '/not/started',
    fontPath: '/not/read',
  })
  await assert.rejects(
    inputRenderer.render(largeInput),
    PdfRenderSizeLimitError,
  )

  const fake = await fakeExecutable(
    [
      "process.stdin.resume()",
      "process.stdin.on('end',()=>{process.stdout.write('%PDF-');process.stdout.write(Buffer.alloc(10*1024*1024))})",
    ].join(';'),
  )
  try {
    const outputRenderer = new CountryOutagePdfRenderer({
      pythonExecutable: fake.executable,
      fontPath: '/trusted/configured/font.ttf',
      timeoutMs: 10_000,
    })
    await assert.rejects(
      outputRenderer.render(reportDocument()),
      PdfRenderSizeLimitError,
    )
  } finally {
    await rm(fake.directory, { recursive: true, force: true })
  }
})
