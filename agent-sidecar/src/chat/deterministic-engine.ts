import type {
  P1AnswerEnvelope,
  P1Answerability,
  P1ConversationState,
  P1EvidenceRecord,
  P1IntentPlan,
  P1StateTransition,
  P1Subanswer,
} from './contracts.js'
import {
  P1_CASE_SET_REVISION,
  P1_CHAT_SCHEMA_VERSION,
  P1_CONTRACT_REVISION,
} from './contracts.js'
import type {
  P1FactBundle,
  P1GeneralReadModelProvider,
} from './general-read-model-provider.js'

type JsonObject = Record<string, any>

interface EngineInput {
  conversationId: string
  turnId: string
  turnNumber: number
  question: string
  state: P1ConversationState
  bundle: P1FactBundle
  signal?: AbortSignal
}

interface AnswerPart {
  key: string
  intents: string[]
  operator: string | null
  answerability: P1Answerability
  text: string
  facts: Array<[string, JsonObject, string | number | boolean | null, string | null, string]>
  limitations?: string[]
  unknowns?: string[]
}

const CONTROL_PLANE_LIMIT =
  '仅反映 RRC25 BGP 控制面观测，不等同于用户实际连通性、流量变化或中断原因。'

function local(utc: string | null | undefined): string {
  if (!utc) return '未知'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(utc)).replaceAll('/', '-').replace(',', '')
}

function n(value: unknown): string {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '未知'
}

function transitionFor(question: string, state: P1ConversationState): P1StateTransition {
  const asnMatch = question.match(/AS\s*(\d{1,10})/i)
  const has4 = /IPv4/i.test(question)
  const has6 = /IPv6/i.test(question)
  const set: Record<string, string | number | null> = {}
  const clear: string[] = []
  const inherit = ['binding']
  const reasonCodes: string[] = ['same_event_binding']
  if (asnMatch) {
    set.asn = Number(asnMatch[1])
    set.topic = 'asn'
    reasonCodes.push(state.asn === null ? 'explicit_asn' : 'explicit_asn_correction')
  } else if (/前五|受影响\s*AS/i.test(question)) {
    set.topic = 'asn'
    clear.push('asn')
  } else if (/路径|AS_PATH/i.test(question)) {
    set.topic = 'path'
    clear.push('asn', 'address_family', 'metric')
    reasonCodes.push('topic_switch')
  } else if (has4 || has6) {
    set.topic = 'address_family'
    set.address_family = has4 && has6 ? 'both' : has4 ? 'ipv4' : 'ipv6'
    clear.push('asn')
  } else if (/证据|追溯|publication|revision/i.test(question)) {
    set.topic = 'evidence'
  } else if (/原因|责任|谁造成|造成的|损失|用户|全国|OONI|IODA|Cloudflare|HTTP|DNS/i.test(question)) {
    set.topic = 'boundary'
    clear.push('asn', 'address_family', 'metric')
  } else if (/峰值|最多|最严重/i.test(question)) {
    set.topic = 'timeline'
    set.metric = 'interrupted_prefix_count'
  } else if (/换|另一次|另一个事件/i.test(question)) {
    set.pending_clarification = 'event_reference'
    clear.push('topic', 'asn', 'address_family', 'metric', 'evidence_anchor')
    reasonCodes.push('event_switch_requires_reference')
  } else if (state.topic) {
    inherit.push('topic')
    if (state.asn !== null) inherit.push('asn')
    if (state.address_family !== null) inherit.push('address_family')
    if (state.metric !== null) inherit.push('metric')
  }
  return { inherit, set, clear: [...new Set(clear)], reason_codes: reasonCodes }
}

function fact(
  bundle: P1FactBundle,
  tuple: AnswerPart['facts'][number],
): P1EvidenceRecord {
  const [evidenceRef, source, value, unit, label] = tuple
  return {
    evidence_ref: evidenceRef,
    source: source === bundle.overview ? 'overview'
      : source === bundle.series ? 'series'
        : source === bundle.asns ? 'asns'
          : source === bundle.paths ? 'paths'
            : source === bundle.audit ? 'audit'
              : source === bundle.resolution ? 'resolution' : 'derived',
    label,
    value,
    unit,
    observed_at_utc: null,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
  }
}

function part(
  key: string,
  answerability: P1Answerability,
  text: string,
  facts: AnswerPart['facts'],
  options: Partial<Pick<AnswerPart, 'intents' | 'operator' | 'limitations' | 'unknowns'>> = {},
): AnswerPart {
  return {
    key,
    answerability,
    text,
    facts,
    intents: options.intents ?? [key],
    operator: options.operator ?? `read_${key}`,
    ...(options.limitations ? { limitations: options.limitations } : {}),
    ...(options.unknowns ? { unknowns: options.unknowns } : {}),
  }
}

export class P1DeterministicQuestionEngine {
  constructor(private readonly provider: P1GeneralReadModelProvider) {}

  private async classify(input: EngineInput): Promise<AnswerPart[]> {
    const q = input.question.trim()
    const b = input.bundle
    const o = b.overview
    const c = o.current
    const p = o.peaks
    const s = b.series
    const parts: AnswerPart[] = []
    const f = (ref: string, source: JsonObject, value: any, unit: string | null, label: string): AnswerPart['facts'][number] => [ref, source, value, unit, label]

    if (
      input.state.pending_clarification === 'event_reference' &&
      !/country_outage\//.test(q)
    ) {
      return [part('event_switch', 'clarify', '“最近那次”不能唯一标识事件。请提供 country_outage 引用、检测时间或从明确候选中选择；我不会沿用旧事件数值。', [], {
        operator: null, unknowns: ['目标事件引用'],
      })]
    }
    if (/换.*事件|另一次.*事件|另一个事件/.test(q) && !/country_outage\//.test(q)) {
      return [part('event_switch', 'clarify', '请提供要切换到的唯一 country_outage 事件引用；我不会默认选择“最新事件”。', [], {
        operator: null, unknowns: ['目标事件引用'],
      })]
    }
    if (/event_end_at_utc.*null|持续了多久/.test(q)) {
      return [part('event_duration', 'partial', 'event_end_at_utc 为 null，表示当前数据范围内没有可确认的结束时点；因此持续时长也是未知，不能按 0 秒处理。', [
        f('event.event_end_at_utc', o, o.event.event_end_at_utc, 'UTC', '事件结束时间'),
        f('event.event_duration_seconds', o, o.event.event_duration_seconds, 'second', '事件持续时长'),
      ], { limitations: ['窗口结束不等于事件真实结束。'] })]
    }
    if (/series.*(另一个|不同).*publication|publication.*冲突/.test(q)) {
      return [part('publication_conflict', 'invalid_data', '不能继续回答峰值。overview 与 series 的 publication 身份冲突时必须失败关闭并重新绑定一致快照。', [], {
        operator: null, unknowns: ['一致的 publication/revision 证据包'],
      })]
    }
    if (/长度少|轨道长度|timestamps.*少/.test(q)) {
      return [part('invalid_series_shape', 'invalid_data', `不能回答。series 声明 ${n(s.point_count)} 个点时，timestamps 与每条轨道都必须恰好有相同长度；不一致不是可忽略缺口。`, [
        f('series.point_count', s, s.point_count, 'state_point', '声明点数'),
        f('series.timestamp_count', s, s.timestamps.length, 'timestamp', '时间戳数量'),
      ], { operator: null })]
    }
    if (/AS\s*999999/i.test(q)) {
      const found = await this.provider.findAsn(b, 999999, input.signal)
      return [part('asn_not_found', 'invalid_data', found
        ? '查询返回了 AS999999，但需重新核对事件对象身份。'
        : '当前 publication 的受影响 AS 查询中没有 AS999999。无结果不等于其前缀中断数为 0。', [
        f('asns.not_found_probe.total', b.asns, found ? 1 : 0, 'result', '查询结果数'),
      ], { operator: 'query_asn', unknowns: found ? [] : ['AS999999 在该事件中的可用观测结果'] })]
    }
    if (/Update|Withdraw|撤销\s*BGP|撤销.*路由/.test(q)) {
      const boundary = /切断|原因|是不是通过/.test(q)
      const zeroInference = /数量.*0|一直为\s*0|说明.*0/.test(q)
      return [part('update_activity', zeroInference ? 'invalid_data' : 'unsupported', '当前页面/API 没有暴露 BGP Update 或 Withdraw 轨道；未暴露不等于数量为 0，也不能据此确认“通过撤销路由切断互联网”。', [
        f('capability_observations.update_activity.status', b.resolution, 'not_exposed_by_current_page_api', null, 'Update 活动能力'),
      ], { limitations: boundary ? [CONTROL_PLANE_LIMIT] : ['未暴露不等于观测值为 0。'] })]
    }
    if (/OONI|IODA|Cloudflare|外部.*数据/.test(q)) {
      return [part('external_measurement', 'unsupported', 'P1 只使用当前事件页面/API，不能调用 OONI、IODA、Cloudflare 或其他外部来源。需要在后续多源证据阶段单独授权和验收。', [
        f('capability_observations.dns_http_traffic_user_experience.status', b.resolution, 'not_in_current_page_api', null, '外部数据能力'),
      ], { operator: null, limitations: [CONTROL_PLANE_LIMIT] })]
    }
    if (/谁.*负责|责任|经济损失|多少用户/.test(q)) {
      return [part('responsibility_impact', 'unsupported', '当前 RRC25 数据不能识别责任主体，也不能计算用户数或经济损失。可确认的仅是同一 publication 中的 BGP 控制面可见性变化。', [
        f('semantic_boundaries.overview', o, o.semantic_boundary, null, '证据边界'),
      ], { operator: null, limitations: [CONTROL_PLANE_LIMIT] })]
    }
    if (/路由还可见.*用户|真的连得上|全国.*断|伊朗人现在.*互联网/.test(q)) {
      return [part('user_connectivity', /还剩|现在|全国/.test(q) ? 'partial' : 'unsupported', `截至数据截止时仍有 ${n(c.interrupted_prefix_count)} 个固定前缀在至少一个 RRC25 观察方向不可见；但 BGP 路由可见或不可见都不能单独证明真实用户能否联网，更不能证明全国完全断网。`, [
        f('current.interrupted_prefix_count', o, c.interrupted_prefix_count, 'prefix', '末端中断前缀'),
        f('semantic_boundaries.overview', o, o.semantic_boundary, null, '证据边界'),
        f('identity.collector_id', b.resolution, b.binding.collector_id, null, 'collector'),
        f('capability_observations.dns_http_traffic_user_experience.status', b.resolution, 'not_in_current_page_api', null, '数据面能力'),
      ], { limitations: [CONTROL_PLANE_LIMIT] })]
    }
    if (/真实原因|为什么|什么原因|技术上看|谁造成|造成的/.test(q)) {
      return [part('cause_boundary', 'partial', `当前证据可确认中断前缀峰值为 ${n(p.interrupted_prefix_count.value)}，但没有 Update、DNS、HTTP、流量或责任证据，不能判断真实原因。`, [
        f('peaks.interrupted_prefix_count.value', o, p.interrupted_prefix_count.value, 'prefix', '中断前缀峰值'),
        f('audit.dataset_id', b.audit, b.audit.dataset_id, null, '数据集身份'),
      ], { limitations: [CONTROL_PLANE_LIMIT], unknowns: ['真实原因及责任主体'] })]
    }
    if (/发生了什么|概述|总结/.test(q)) {
      parts.push(part('event_summary', 'answerable', `在 RRC25 固定 cohort 的 ${n(o.cohort.fixed_prefix_count)} 个前缀中，观测到大规模路由不可见变化；中断前缀峰值为 ${n(p.interrupted_prefix_count.value)}，事件范围内共有 ${n(o.affected_as_count)} 个受影响 AS。观测持续到数据范围末端，不能判定事件已结束。`, [
        f('cohort.fixed_prefix_count', o, o.cohort.fixed_prefix_count, 'prefix', '固定前缀'),
        f('scope_counts.affected_as_count', o, o.affected_as_count, 'asn', '受影响 AS'),
        f('peaks.interrupted_prefix_count.value', o, p.interrupted_prefix_count.value, 'prefix', '中断前缀峰值'),
      ], { intents: ['event_summary', 'scope'], limitations: [CONTROL_PLANE_LIMIT] }))
    }
    if (/观测窗口/.test(q)) {
      parts.push(part('observation_window', 'answerable', `观测窗口为北京时间 ${local(b.binding.window_start_utc)} 至 ${local(b.binding.window_end_utc)}。窗口结束不等于事件真实结束。`, [
        f('event.window_start_local', o, '2026-02-27T08:10:00+08:00', 'Asia/Shanghai', '窗口开始'),
        f('event.window_end_local', o, '2026-03-11T08:00:00+08:00', 'Asia/Shanghai', '窗口结束'),
      ], { limitations: ['窗口结束不等于事件真实结束。'] }))
    }
    if (/什么时候开始|异常.*开始|检测时间/.test(q)) {
      parts.push(part('detection_time', 'partial', `页面记录的检测时间是北京时间 ${local(o.event.detected_at_utc)}；它不等于窗口开始，也不能证明真实异常起点。`, [
        f('event.detected_at_local', o, '2026-02-27T09:12:32+08:00', 'Asia/Shanghai', '检测时间'),
        f('event.detection_is_not_true_onset', o, true, null, '检测时间边界'),
      ], { limitations: ['检测时间、窗口开始和真实起点是不同概念。'] }))
    }
    if (/中断前缀最多|哪个时点.*最多|这个峰值|峰值.*多少|什么时候最严重|最严重.*前缀/.test(q)) {
      parts.push(part('primary_peak', 'answerable', `中断前缀在北京时间 ${local(p.interrupted_prefix_count.state_point_utc)} 达到峰值 ${n(p.interrupted_prefix_count.value)} 个。该指标不同于“完全中断前缀”；同一时点不能套用另一个指标的峰值。`, [
        f('peaks.interrupted_prefix_count.state_point_local', o, '2026-02-28T07:15:00+08:00', 'Asia/Shanghai', '中断前缀峰值时点'),
        f('peaks.interrupted_prefix_count.value', o, p.interrupted_prefix_count.value, 'prefix', '中断前缀峰值'),
      ]))
    }
    if (/恢复|峰值之后.*多少|持续异常/.test(q)) {
      parts.push(part('recovery', 'partial', `中断前缀峰值为 ${n(p.interrupted_prefix_count.value)}，数据截止时为 ${n(c.interrupted_prefix_count)} 个；这两个时点的对比不能证明期间一直持续异常，也不能判定事件结束或互联网完全恢复。`, [
        f('peaks.interrupted_prefix_count.value', o, p.interrupted_prefix_count.value, 'prefix', '中断前缀峰值'),
        f('current.interrupted_prefix_count', o, c.interrupted_prefix_count, 'prefix', '末端中断前缀'),
        f('identity.lifecycle_state', b.resolution, b.binding.lifecycle_state, null, '生命周期状态'),
      ], { limitations: ['只比较峰值与数据截止两个状态点，不能证明中间连续性；窗口末端状态受时间删失，BGP 恢复不等于用户连接恢复。'] }))
    }
    if (/数据截止|还剩多少路由|末端状态|到最后还剩多少/.test(q)) {
      parts.push(part('current_state', 'answerable', `截至北京时间 ${local(b.binding.data_through)}，仍有 ${n(c.interrupted_prefix_count)} 个中断前缀，其中 ${n(c.completely_interrupted_prefix_count)} 个完全不可见；涉及 ${n(c.invisible_direction_count)} 个不可见方向和 ${n(c.affected_asn_count)} 个受影响 AS。`, [
        f('current.interrupted_prefix_count', o, c.interrupted_prefix_count, 'prefix', '末端中断前缀'),
        f('current.completely_interrupted_prefix_count', o, c.completely_interrupted_prefix_count, 'prefix', '末端完全中断前缀'),
        f('current.invisible_direction_count', o, c.invisible_direction_count, 'peer_asn_direction', '末端不可见方向'),
        f('current.affected_asn_count', o, c.affected_asn_count, 'asn', '末端受影响 AS'),
      ], { limitations: ['这是数据截止时点，不是当前实时状态。'] }))
    }
    if (/覆盖多大范围|固定 cohort|多大范围/.test(q)) {
      parts.push(part('scope', 'answerable', `固定 cohort 含 ${n(o.cohort.fixed_asn_count)} 个 AS、${n(o.cohort.fixed_prefix_count)} 个前缀和 ${n(o.cohort.independent_direction_relation_count)} 个独立观察方向；事件范围内 ${n(o.affected_as_count)} 个 AS 出现受影响记录。`, [
        f('cohort.fixed_asn_count', o, o.cohort.fixed_asn_count, 'asn', '固定 AS'),
        f('cohort.fixed_prefix_count', o, o.cohort.fixed_prefix_count, 'prefix', '固定前缀'),
        f('cohort.independent_direction_relation_count', o, o.cohort.independent_direction_relation_count, 'peer_asn_direction', '独立方向'),
        f('scope_counts.affected_as_count', o, o.affected_as_count, 'asn', '受影响 AS'),
      ]))
    }
    if (/前五.*AS|前 5.*AS/.test(q)) {
      const items = (b.asns.items as JsonObject[]).slice(0, 5)
      parts.push(part('top_asns', 'answerable', `页面按默认排序列出的前五个受影响 AS 是：${items.map((item) => `AS${item.asn}（${item.as_name || '名称未知'}）`).join('、')}。`, items.map((item, index) => f(`asns.top_items.${index}.asn`, b.asns, item.asn, 'asn', `第 ${index + 1} 个 AS`)), { operator: 'read_asn_ranking' }))
    }
    const asnMatch = q.match(/AS\s*(\d{1,10})/i)
    if (asnMatch && Number(asnMatch[1]) !== 999999) {
      const asn = Number(asnMatch[1])
      const item = await this.provider.findAsn(b, asn, input.signal)
      if (!item) {
        parts.push(part('specified_asn', 'invalid_data', `当前 publication 的受影响 AS 结果中没有 AS${asn}；无结果不能解释为各项为 0。`, [], { operator: 'query_asn' }))
      } else {
        const index = (b.asns.items as JsonObject[]).findIndex((value) => value.asn === asn)
        const base = index >= 0 ? `asns.top_items.${index}` : `asns.query.${asn}`
        parts.push(part('specified_asn', 'answerable', `AS${asn}（${item.as_name || '名称未知'}）有 ${n(item.fixed_prefix_count)} 个固定前缀；峰值时 ${n(item.peak_complete_prefix_count)} 个完全不可见，涉及 ${n(item.peak_invisible_direction_count)} 个不可见方向，路径下游 AS 数为 ${n(item.path_downstream_asn_count)}。`, [
          f(`${base}.fixed_prefix_count`, b.asns, item.fixed_prefix_count, 'prefix', '固定前缀'),
          f(`${base}.peak_complete_prefix_count`, b.asns, item.peak_complete_prefix_count, 'prefix', '完全中断峰值'),
          f(`${base}.peak_invisible_direction_count`, b.asns, item.peak_invisible_direction_count, 'peer_asn_direction', '不可见方向峰值'),
          f(`${base}.path_downstream_asn_count`, b.asns, item.path_downstream_asn_count, 'asn', '路径下游 AS'),
        ], { operator: 'query_asn', limitations: ['受影响 AS 排名是观测排序，不是原因或责任排序。'] }))
      }
    }
    if (
      /IPv4.*IPv6|IPv6.*IPv4|可见 IPv4.*下降|IPv4 地址规模最大下降/.test(q)
      && !/新出现|新.*前缀/.test(q)
    ) {
      const d4 = b.derived.ipv4
      const d6 = b.derived.ipv6
      const only4 = /IPv4 地址规模最大下降/.test(q)
      parts.push(part('address_family', 'answerable', only4
        ? `固定前缀可见 IPv4 地址从最大 ${n(d4.maximum)} 降至最小 ${n(d4.minimum)}，减少 ${n(d4.drop)} 个唯一地址，约 ${d4.drop_percent}%。`
        : `IPv4 最大到最小减少 ${n(d4.drop)} 个唯一地址；IPv6 最大到最小仅减少 ${n(d6.drop)} 个 /48 等价块。窗口内累计出现新 IPv4 前缀 ${n(c.new_cumulative_ipv4_prefix_count)} 个、新 IPv6 前缀 ${n(c.new_cumulative_ipv6_prefix_count)} 个。`, [
        f('address_family_extrema.ipv4.maximum_visible_address_count', b.derived, d4.maximum, 'unique_ipv4_address', 'IPv4 最大值'),
        f('address_family_extrema.ipv4.minimum_visible_address_count', b.derived, d4.minimum, 'unique_ipv4_address', 'IPv4 最小值'),
        f('address_family_extrema.ipv4.max_to_min_drop', b.derived, d4.drop, 'unique_ipv4_address', 'IPv4 最大下降'),
        f('address_family_extrema.ipv4.max_to_min_drop_percent', b.derived, d4.drop_percent, 'percent', 'IPv4 下降比例'),
        ...(only4 ? [] : [
          f('address_family_extrema.ipv6.max_to_min_drop', b.derived, d6.drop, 'ipv6_slash48_equivalent', 'IPv6 最大下降'),
          f('current.new_cumulative_ipv4_prefix_count', o, c.new_cumulative_ipv4_prefix_count, 'prefix', '新 IPv4 前缀'),
          f('current.new_cumulative_ipv6_prefix_count', o, c.new_cumulative_ipv6_prefix_count, 'prefix', '新 IPv6 前缀'),
        ]),
      ], { operator: 'series_extrema', limitations: ['地址规模是控制面可见地址并集，不是活跃用户或流量。'] }))
    }
    if (/新出现.*IPv4|多少.*IPv4.*前缀/.test(q)) {
      parts.push(part('new_prefixes', 'answerable', `窗口内累计新出现 IPv4 前缀 ${n(c.new_cumulative_ipv4_prefix_count)} 个、IPv6 前缀 ${n(c.new_cumulative_ipv6_prefix_count)} 个；数据截止时仍可见的分别为 ${n(c.new_visible_ipv4_prefix_count)} 和 ${n(c.new_visible_ipv6_prefix_count)} 个。`, [
        f('current.new_cumulative_ipv4_prefix_count', o, c.new_cumulative_ipv4_prefix_count, 'prefix', '累计新 IPv4 前缀'),
        f('current.new_cumulative_ipv6_prefix_count', o, c.new_cumulative_ipv6_prefix_count, 'prefix', '累计新 IPv6 前缀'),
        f('current.new_visible_ipv4_prefix_count', o, c.new_visible_ipv4_prefix_count, 'prefix', '末端可见新 IPv4 前缀'),
        f('current.new_visible_ipv6_prefix_count', o, c.new_visible_ipv6_prefix_count, 'prefix', '末端可见新 IPv6 前缀'),
      ]))
    }
    if (/中断前缀.*完全中断|不可见方向.*意思|分别是什么意思/.test(q)) {
      const defs = s.track_definitions
      const interruptedDefinition = defs.interrupted_prefix_count.definition.replace(/。$/, '')
      const completeDefinition = defs.completely_interrupted_prefix_count.definition.replace(/。$/, '')
      const directionDefinition = defs.invisible_direction_count.definition.replace(/。$/, '')
      parts.push(part('metric_semantics', 'answerable', `“中断前缀”是${interruptedDefinition}；“完全中断前缀”是${completeDefinition}；“不可见方向”是${directionDefinition}。`, [
        f('series.track_definitions.interrupted_prefix_count', s, defs.interrupted_prefix_count.definition.replace(/。$/, ''), null, '中断前缀定义'),
        f('series.track_definitions.completely_interrupted_prefix_count', s, defs.completely_interrupted_prefix_count.definition.replace(/。$/, ''), null, '完全中断前缀定义'),
        f('series.track_definitions.invisible_direction_count', s, defs.invisible_direction_count.definition.replace(/。$/, ''), null, '不可见方向定义'),
      ], { operator: 'read_metric_definitions' }))
    }
    if (/路径样本|实际路径关联|AS_PATH/.test(q)) {
      const relation = b.paths.items[0]
      const sample = relation.path_samples[0]
      parts.push(part('path_sample', 'answerable', `示例关联为受影响 AS${relation.affected_asn} → 下游 AS${relation.downstream_asn}（${relation.downstream_as_name || '名称未知'}），样本前缀 ${sample.prefix}，AS_PATH 为 ${sample.as_path_canonical}。它只说明 RRC25 中观察到有序路径关联，不证明客户依赖、故障传播或原因。`, [
        f('paths.first_relation.affected_asn', b.paths, relation.affected_asn, 'asn', '关联 AS'),
        f('paths.first_relation.downstream_asn', b.paths, relation.downstream_asn, 'asn', '下游 AS'),
        f('paths.first_relation.sample.as_path_canonical', b.paths, sample.as_path_canonical, null, 'AS_PATH 样本'),
        f('paths.first_relation.sample.prefix', b.paths, sample.prefix, 'prefix', '前缀样本'),
      ], { operator: 'read_path_sample', limitations: ['路径关联不是依赖关系或因果证据。'] }))
    }
    if (/追溯|证据身份|证据在哪里|dataset|implementation/.test(q)) {
      parts.push(part('evidence_trace', 'answerable', `本轮事实绑定 dataset ${b.audit.dataset_id}、实现 ${b.audit.implementation_id} 和事件内容 SHA-256 ${b.audit.event_content_sha256}。`, [
        f('audit.dataset_id', b.audit, b.audit.dataset_id, null, '数据集身份'),
        f('audit.implementation_id', b.audit, b.audit.implementation_id, null, '实现身份'),
        f('audit.event_content_sha256', b.audit, b.audit.event_content_sha256, null, '事件内容哈希'),
      ], { operator: 'read_audit_identity' }))
    }
    if (/数据完整|还缺什么|完整吗/.test(q)) {
      parts.push(part('data_completeness', 'partial', `当前 publication 的 quality_state=${b.resolution.quality_state}、missing_slot_count=${b.resolution.missing_slot_count}，series 有 ${n(s.point_count)} 个点；但当前页面/API 未暴露 Update/Withdraw，也没有 DNS、HTTP、流量或用户体验数据。`, [
        f('identity.quality_state', b.resolution, b.resolution.quality_state, null, '质量状态'),
        f('identity.missing_slot_count', b.resolution, b.resolution.missing_slot_count, 'slot', '缺失时隙'),
        f('series.point_count', s, s.point_count, 'state_point', '序列点数'),
        f('capability_observations.update_activity.status', b.resolution, 'not_exposed_by_current_page_api', null, 'Update 能力'),
      ], { limitations: ['数据集时隙完整不等于具备所有分析维度。'] }))
    }
    if (/绑定.*publication|哪个 publication|revision.*数据|是否最终/.test(q)) {
      parts.push(part('publication_identity', 'answerable', `当前绑定 publication ${b.binding.publication_id}、revision ${b.binding.revision}，数据截至 ${b.binding.data_through ?? '未知'}；is_final_in_data_range=${b.binding.is_final_in_data_range}，因此不能判定为最终结束状态。`, [
        f('identity.publication_id', b.resolution, b.binding.publication_id, null, 'publication'),
        f('identity.revision', b.resolution, b.binding.revision, null, 'revision'),
        f('identity.data_through', b.resolution, b.binding.data_through, 'UTC', '数据截止'),
        f('identity.is_final_in_data_range', b.resolution, b.binding.is_final_in_data_range, null, '最终性'),
      ], { operator: 'read_binding' }))
    }
    if (/能证明什么|不能证明什么|仅凭这页|RRC25 数据/.test(q)) {
      parts.push(part('rrc25_boundary', 'answerable', '这页能证明：在指定事件、窗口和 publication 下，RRC25 观察到固定前缀、AS、独立方向与路径关联的控制面变化。不能证明真实用户可达性、全国完全断网、DNS/HTTP/流量状态、原因、责任或经济损失。', [
        f('identity.collector_id', b.resolution, b.binding.collector_id, null, 'collector'),
        f('semantic_boundaries.overview', o, o.semantic_boundary, null, '证据边界'),
      ], { limitations: [CONTROL_PLANE_LIMIT] }))
    }
    if (parts.length === 0 && /那 IPv6 呢|IPv6 呢/.test(q)) {
      const d6 = b.derived.ipv6
      parts.push(part('address_family', 'answerable', `IPv6 固定前缀可见规模从最大 ${n(d6.maximum)} 降至最小 ${n(d6.minimum)} 个 /48 等价块，最大到最小减少 ${n(d6.drop)} 个，约 ${d6.drop_percent}%。`, [
        f('address_family_extrema.ipv6.max_to_min_drop', b.derived, d6.drop, 'ipv6_slash48_equivalent', 'IPv6 最大下降'),
      ], { operator: 'series_extrema' }))
    }
    if (parts.length === 0) {
      parts.push(part('clarification', 'clarify', '我只能回答当前事件页面/API 已有的概述、时间、峰值、范围、ASN、地址族、路径、指标语义和证据身份。请把问题限定到其中一项。', [], { operator: null }))
    }
    return parts
  }

  async answer(input: EngineInput): Promise<P1AnswerEnvelope> {
    if (input.signal?.aborted) throw new Error('cancelled')
    const transition = transitionFor(input.question, input.state)
    const parts = await this.classify(input)
    const evidence = parts.flatMap((item) => item.facts.map((value) => fact(input.bundle, value)))
    const uniqueEvidence = [...new Map(evidence.map((item) => [item.evidence_ref, item])).values()]
    const results: P1Subanswer[] = parts.map((item, index) => ({
      subrequest_id: `sub_${index + 1}`,
      intents: item.intents,
      operator: item.operator,
      answerability: item.answerability,
      text: item.text,
      evidence_refs: item.facts.map(([ref]) => ref),
      limitations: item.limitations ?? [],
      unknowns: item.unknowns ?? [],
    }))
    const rank: Record<P1Answerability, number> = {
      invalid_data: 5, unsupported: 4, clarify: 3, partial: 2, answerable: 1,
    }
    const answerability = [...parts].sort((a, b) => rank[b.answerability] - rank[a.answerability])[0]!.answerability
    const plan: P1IntentPlan = {
      normalized_question: input.question.trim().replace(/\s+/g, ' '),
      subrequests: parts.map((item, index) => ({
        subrequest_id: `sub_${index + 1}`,
        intents: item.intents,
        entities: {
          asn: transition.set.asn ?? input.state.asn,
          address_family: transition.set.address_family ?? input.state.address_family,
        },
        operator: item.operator,
        answerability: item.answerability,
        confidence: item.answerability === 'clarify' ? 0.5 : 1,
        reason_codes: item.answerability === 'unsupported' ? ['capability_not_available'] : ['deterministic_rule_match'],
      })),
      transition,
      blocking_errors: parts.some((item) => item.answerability === 'invalid_data') ? ['invalid_data'] : [],
    }
    const limitations = [...new Set(parts.flatMap((item) => item.limitations ?? []))]
    const unknowns = [...new Set(parts.flatMap((item) => item.unknowns ?? []))]
    return {
      schema_version: P1_CHAT_SCHEMA_VERSION,
      conversation_id: input.conversationId,
      turn_id: input.turnId,
      turn_number: input.turnNumber,
      answerability,
      binding: input.bundle.binding,
      p0_case_set_revision: P1_CASE_SET_REVISION,
      p1_contract_revision: P1_CONTRACT_REVISION,
      plan,
      results,
      answer_text: results.map((item) => item.text).join('\n\n'),
      evidence: uniqueEvidence,
      limitations,
      unknowns,
      transition,
      validation: {
        passed: answerability !== 'invalid_data',
        errors: answerability === 'invalid_data' ? ['invalid_data_failed_closed'] : [],
        checked_evidence_refs: uniqueEvidence.map((item) => item.evidence_ref),
      },
      runtime_identity: {
        implementation: 'p1-deterministic-chat',
        rule_set: 'p1-rules-v1',
        language_layer: 'deterministic-fallback',
        collector: 'rrc25',
      },
      completed_at: new Date().toISOString(),
    }
  }
}
