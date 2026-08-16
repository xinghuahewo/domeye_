<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getAsOverview, getAsRecentEvents, getASPrefixOutages, type FeatureRange } from '@/api/features'
import EventTable from '@/components/EventTable.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import SparklinePair from '@/components/SparklinePair.vue'
import type { AsnProfile, AsOverview, EventRow, OutagePoint } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { parseInputTime, recentRange, toBackendTime, toInputTime } from '@/utils/time'

const route = useRoute()
const router = useRouter()
const defaults = recentRange(24)
const query = reactive({ start: defaults.start, end: defaults.end })
const asnInput = ref('')
const overview = ref<AsOverview | null>(null)
const prefixOutages = ref<OutagePoint[]>([])
const recentEvents = ref<EventRow[]>([])
const loading = ref(false)
const error = ref('')
const outageLoading = ref(false)
const outageError = ref('')
const eventsLoading = ref(false)
const eventError = ref('')
let loadToken = 0

const selectedAsn = computed(() => {
  const value = route.params.asn
  if (typeof value !== 'string') return ''
  return value.trim().replace(/^AS/i, '')
})
const selected = computed(() => overview.value?.selectedAsn ?? null)
const eventContext = computed(() => {
  const start = typeof route.query.event_start === 'string' ? route.query.event_start : ''
  const end = typeof route.query.event_end === 'string' ? route.query.event_end : ''
  const reference = typeof route.query.event_ref === 'string' ? route.query.event_ref : ''
  if (!start || !end || !reference) return null
  const startDate = new Date(start)
  const endDate = new Date(end)
  if (
    Number.isNaN(startDate.getTime())
    || Number.isNaN(endDate.getTime())
    || startDate.getTime() >= endDate.getTime()
  ) return null
  return { start, end, reference, startDate, endDate }
})

const returnEventLink = computed(() => ({
  name: 'event-detail',
  query: {
    ref: eventContext.value?.reference || '',
    focus: typeof route.query.return_anchor === 'string' ? route.query.return_anchor : 'affected-as',
    as_page: typeof route.query.as_page === 'string' ? route.query.as_page : undefined,
    as_query: typeof route.query.as_query === 'string' ? route.query.as_query : undefined,
    as_classification: typeof route.query.as_classification === 'string'
      ? route.query.as_classification
      : undefined,
  },
}))

function eventWindowLabel(): string {
  if (!eventContext.value) return ''
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  return `${formatter.format(eventContext.value.startDate)} — ${formatter.format(eventContext.value.endDate)}`
}

const selectedSeries = computed(() => {
  const source = selected.value?.series ?? []
  if (!eventContext.value) return source
  const start = parseInputTime(query.start)
  const end = parseInputTime(query.end)
  if (!start || !end) return source
  const byTime = new Map(source.map((point) => [point.time, point]))
  const result = []
  for (
    let cursor = start.getTime();
    cursor <= end.getTime();
    cursor += 5 * 60 * 1000
  ) {
    const time = toBackendTime(toInputTime(new Date(cursor)))
    result.push(byTime.get(time) ?? {
      time,
      announce: null,
      withdraw: null,
      ipv4Prefixes: null,
      ipv6Prefixes: null,
      ipv4Addresses: null,
    })
  }
  return result
})

function asnRoute(asn: string) {
  return {
    name: 'asn-detail',
    params: { asn },
    query: eventContext.value ? { ...route.query } : {},
  }
}

const asnSuggestions = computed(() => {
  const profiles = new Map<string, AsnProfile>()
  for (const ranking of [
    overview.value?.updateRankings,
    overview.value?.withdrawRateRankings,
    overview.value?.anomalyRankings,
  ]) {
    for (const profile of ranking ?? []) profiles.set(profile.asn, profile)
  }
  return [...profiles.values()]
})

const rankingSections = computed(() => [
  {
    key: 'updates',
    index: '01',
    title: '更新量最高',
    note: 'ANNOUNCE + WITHDRAW',
    rows: overview.value?.updateRankings ?? [],
    value: (profile: AsnProfile) => formatNumber(profile.updateTotal),
    unit: '条',
  },
  {
    key: 'withdraw',
    index: '02',
    title: '撤回率最高',
    note: 'WITHDRAW / UPDATES',
    rows: overview.value?.withdrawRateRankings ?? [],
    value: (profile: AsnProfile) => `${profile.withdrawRate.toFixed(1)}%`,
    unit: '',
  },
  {
    key: 'anomaly',
    index: '03',
    title: '异常事件最多',
    note: 'SIX CORE CLASSES',
    rows: overview.value?.anomalyRankings ?? [],
    value: (profile: AsnProfile) => formatNumber(profile.anomalyCount),
    unit: '起',
  },
])

const messageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: selectedSeries.value.map((point) => [point.time, point.announce]),
  },
  {
    name: 'WITHDRAW',
    color: '#35b6d4',
    data: selectedSeries.value.map((point) => [point.time, point.withdraw]),
  },
])

const resourceSeries = computed<ChartSeries[]>(() => [
  {
    name: 'IPv4 /24 SEGMENTS',
    color: '#175cd3',
    data: selectedSeries.value
      .map((point) => [point.time, point.ipv4Prefixes]),
  },
  {
    name: 'IPv6 /48 SEGMENTS',
    color: '#35b6d4',
    data: selectedSeries.value
      .map((point) => [point.time, point.ipv6Prefixes]),
  },
])

const outageSeries = computed<ChartSeries[]>(() => [{
  name: 'PREFIX OUTAGE',
  color: '#f48120',
  data: prefixOutages.value.map((point) => [point.time, point.count]),
}])

function featureRange(): FeatureRange {
  return { start_time: toBackendTime(query.start), end_time: toBackendTime(query.end) }
}

function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : value.toLocaleString('zh-CN')
}

function changeLabel(value: number | null) {
  if (value === null) return '上一窗口无基线'
  if (value === 0) return '与上一窗口持平'
  return `${value > 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(1)}% 较上一窗口`
}

function displayName(profile: AsnProfile | null | undefined) {
  if (!profile) return '—'
  return `AS${profile.asn}${profile.asName ? ` · ${profile.asName}` : ''}`
}

async function load() {
  const token = ++loadToken
  loading.value = true
  error.value = ''
  outageError.value = ''
  eventError.value = ''
  prefixOutages.value = []
  recentEvents.value = []
  const range = featureRange()
  const asn = selectedAsn.value
  outageLoading.value = Boolean(asn)
  eventsLoading.value = Boolean(asn)
  let overviewReady = false
  try {
    const result = await getAsOverview(
      range,
      asn || undefined,
      6,
      Boolean(eventContext.value),
      eventContext.value?.reference,
    )
    if (token !== loadToken) return
    overview.value = result
    overviewReady = true
  } catch (cause) {
    if (token !== loadToken) return
    overview.value = null
    error.value = errorMessage(cause)
  } finally {
    if (token === loadToken) loading.value = false
  }
  if (!overviewReady || !asn || token !== loadToken) return

  try {
    const result = await getAsRecentEvents(
      asn,
      range,
      10,
      Boolean(eventContext.value),
      eventContext.value?.reference,
    )
    if (token === loadToken) recentEvents.value = result.data
  } catch (cause) {
    if (token === loadToken) eventError.value = errorMessage(cause)
  } finally {
    if (token === loadToken) eventsLoading.value = false
  }
  if (token !== loadToken) return
  try {
    const result = await getASPrefixOutages(asn, range)
    if (token === loadToken) prefixOutages.value = result
  } catch (cause) {
    if (token === loadToken) outageError.value = errorMessage(cause)
  } finally {
    if (token === loadToken) outageLoading.value = false
  }
}

function openAsn(asn?: string) {
  const target = (asn ?? asnInput.value).trim().replace(/^AS/i, '')
  if (!/^\d+$/.test(target)) {
    error.value = '请输入纯数字 ASN 或 AS 加数字，例如 AS3356'
    return
  }
  void router.push(asnRoute(target))
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
}

watch(
  [() => route.params.asn, () => route.query.event_start, () => route.query.event_end],
  () => {
    if (eventContext.value) {
      query.start = toInputTime(eventContext.value.startDate)
      query.end = toInputTime(eventContext.value.endDate)
    } else {
      query.start = defaults.start
      query.end = defaults.end
    }
    asnInput.value = selectedAsn.value ? `AS${selectedAsn.value}` : ''
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <article class="page asn-page">
    <section v-if="eventContext" class="event-window-context" aria-label="国家中断事件窗口">
      <div>
        <span>按国家中断事件窗口查看</span>
        <strong>{{ eventWindowLabel() }}</strong>
      </div>
      <RouterLink :to="returnEventLink">← 返回事件中的相关 AS</RouterLink>
    </section>
    <header class="page-heading asn-heading">
      <div>
        <p class="eyebrow">重点 AS 态势 / ASN desk</p>
        <h1>{{ selectedAsn ? `AS${selectedAsn}` : '重点 ASN 监测台' }}</h1>
      </div>
      <p class="page-heading-copy">
        在可审计的运维候选集内定位 ASN 报文和六类异常；该视图尚未进入 P0 准入，也不代表全网 ASN 排名。
      </p>
    </header>

    <section class="legacy-boundary" aria-label="ASN 数据准入边界">
      <b>LEGACY EXPLORATION · NOT P0 ADMITTED</b>
      <p>本页仅用于对象定位；已移除未准入的 resource_change / max、volatility 和浏览器端样本覆盖率，不与首页 P0 指标混算。</p>
    </section>

    <form class="asn-console" @submit.prevent="openAsn()">
      <label>
        <span>检索 ASN</span>
        <input v-model="asnInput" list="asn-suggestions" placeholder="例如：AS3356、4134" />
        <datalist id="asn-suggestions">
          <option v-for="profile in asnSuggestions" :key="profile.asn" :value="`AS${profile.asn}`">
            {{ profile.asName }} · {{ profile.country }}
          </option>
        </datalist>
      </label>
      <button class="solid-action" type="submit">打开档案</button>
      <RouterLink v-if="selectedAsn" class="text-action" :to="{ name: 'ases' }">返回 ASN 总览</RouterLink>
      <span class="console-freshness">DATA CUT · {{ overview?.latestObservation || '尚无观测' }}</span>
    </form>

    <PageState v-if="loading && !overview" kind="loading" title="正在聚合 ASN 运维候选集" detail="首次进程请求会预热静态 AS 信息，后续查询使用只读缓存" />
    <PageState v-else-if="error" kind="error" title="ASN 态势不可用" :detail="error" @retry="load" />

    <template v-if="overview">
      <section class="scope-note" aria-label="ASN 排行范围说明">
        <div>
          <span>COMPARISON SCOPE</span>
          <strong>{{ overview.scopeSize }} / {{ overview.candidatePoolSize }}</strong>
        </div>
        <p>{{ overview.scopeNote }}</p>
      </section>

      <section class="asn-leaders" aria-label="ASN 态势核心指标">
        <article>
          <span>有特征 ASN</span>
          <strong>{{ overview.featureAsnCount }}</strong>
          <b>/ {{ overview.scopeSize }} 个当前候选</b>
        </article>
        <article>
          <span>重要 ASN</span>
          <strong>{{ overview.importantAsnCount }}</strong>
          <b>STATIC IMPORTANT-AS LABEL</b>
        </article>
        <article>
          <span>存在异常</span>
          <strong>{{ overview.asnsWithAnomalies }}</strong>
          <b>六类核心异常</b>
        </article>
        <article>
          <span>更新量最高</span>
          <strong>{{ displayName(overview.updateLeader) }}</strong>
          <b>{{ formatNumber(overview.updateLeader?.updateTotal) }} 条</b>
        </article>
      </section>

      <section class="ranking-board" aria-label="ASN 候选集排行">
        <article v-for="section in rankingSections" :key="section.key" class="ranking-sheet">
          <header>
            <span>{{ section.index }}</span>
            <div>
              <h2>{{ section.title }}</h2>
              <p>{{ section.note }}</p>
            </div>
          </header>
          <ol>
            <li v-for="(profile, index) in section.rows" :key="profile.asn">
              <span class="rank-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <RouterLink :to="asnRoute(profile.asn)">
                <strong>AS{{ profile.asn }} <em v-if="profile.important">重点</em></strong>
                <small>{{ profile.asName || profile.orgName || '静态名称未知' }} · {{ profile.country || '国家未知' }}</small>
              </RouterLink>
              <SparklinePair :points="profile.sparkline" :label="`AS${profile.asn} 报文趋势`" />
              <b>{{ section.value(profile) }} <small>{{ section.unit }}</small></b>
            </li>
          </ol>
        </article>
      </section>

      <section v-if="selected" class="asn-dossier" aria-labelledby="asn-dossier-title">
        <header class="dossier-heading">
          <div>
            <p>{{ eventContext ? 'SELECTED ASN / EVENT WINDOW DOSSIER' : 'SELECTED ASN / 24H DOSSIER' }}</p>
            <h2 id="asn-dossier-title">AS{{ selected.asn }} · {{ selected.asName || '名称未知' }}</h2>
            <span>{{ selected.orgName || '组织未知' }} · {{ selected.country || '国家未知' }} · {{ selected.asType || '类型未知' }}</span>
          </div>
          <div class="dossier-actions">
            <RouterLink :to="{ name: 'events', query: { attacked_as: selected.asn } }">检索该 ASN 事件 →</RouterLink>
            <span>最后观测 {{ selected.latestObservation || '未知' }}</span>
            <b v-if="selected.important">IMPORTANT AS</b>
          </div>
        </header>

        <div class="identity-ledger">
          <span>全球排名 <b>{{ selected.globalRank ?? '—' }}</b></span>
          <span>国家排名 <b>{{ selected.countryRank ?? '—' }}</b></span>
          <span>样本域 <b>{{ overview.scopeSize }} ASN</b></span>
          <span>证据语义 <b>OBSERVATION, NOT CAUSAL TRACE</b></span>
        </div>

        <div class="dossier-metrics">
          <article>
            <span>更新总量</span>
            <strong>{{ formatNumber(selected.updateTotal) }}</strong>
            <small>{{ changeLabel(selected.updateChangeRate) }}</small>
          </article>
          <article>
            <span>撤回率</span>
            <strong>{{ selected.withdrawRate.toFixed(1) }}%</strong>
            <small>{{ formatNumber(selected.withdraw) }} WITHDRAW</small>
          </article>
          <article>
            <span>IPv4 /24 等效段</span>
            <strong>{{ formatNumber(selected.ipv4Prefixes) }}</strong>
            <small>LEGACY SNAPSHOT · 非 P0 指标</small>
          </article>
          <article>
            <span>IPv6 /48 等效段</span>
            <strong>{{ formatNumber(selected.ipv6Prefixes) }}</strong>
            <small>LEGACY SNAPSHOT · 非 P0 指标</small>
          </article>
          <article>
            <span>异常 / 高风险</span>
            <strong>{{ selected.anomalyCount }} / {{ selected.highRiskCount }}</strong>
            <small>六类核心异常</small>
          </article>
        </div>

        <div class="asn-chart-grid">
          <section class="asn-chart-panel">
            <div class="section-heading"><h3>报文脉冲</h3><span>announce / withdraw</span></div>
            <PageState v-if="selected.series.length === 0" title="当前窗口没有 ASN 报文样本" />
            <LineChart v-else :series="messageSeries" unit="条" :height="300" />
          </section>
          <section class="asn-chart-panel">
            <div class="section-heading"><h3>路由资源等效段</h3><span>legacy snapshot · null ≠ zero · not P0 admitted</span></div>
            <PageState v-if="selected.series.length === 0" title="当前窗口没有资源快照" />
            <LineChart v-else :series="resourceSeries" unit="个" :height="300" />
          </section>
          <section class="asn-chart-panel is-wide">
            <div class="section-heading"><h3>前缀并发中断</h3><span>3-minute active slots</span></div>
            <PageState v-if="outageLoading" kind="loading" title="正在读取 ASN 前缀中断时间槽" />
            <PageState v-else-if="outageError" kind="error" title="ASN 中断时序不可用" :detail="outageError" @retry="load" />
            <LineChart v-else :series="outageSeries" unit="起" :height="270" />
          </section>
        </div>

        <section class="asn-events">
          <div class="section-heading"><h3>最近异常事件</h3><span>{{ recentEvents.length }} latest records</span></div>
          <PageState v-if="eventsLoading" kind="loading" title="正在读取 ASN 最近事件" />
          <PageState v-else-if="eventError" kind="error" title="ASN 事件不可用" :detail="eventError" @retry="load" />
          <PageState v-else-if="recentEvents.length === 0" title="当前窗口没有可展示的 ASN 异常事件" />
          <EventTable v-else compact :events="recentEvents" @select="openEvent" />
        </section>
      </section>

      <PageState v-else title="从排行打开一个 ASN 档案" detail="总览只用于定位，档案页再加载单 ASN 时序、并发中断和最近事件。" />
    </template>
  </article>
</template>

<style scoped>
.asn-heading { align-items: end; }

.event-window-context {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 13px 16px;
  color: #e8f4f8;
  background: #14384a;
  border-left: 4px solid #e27839;
}
.event-window-context div { display: grid; gap: 4px; }
.event-window-context span { color: #91c2d2; font-size: 9px; font-weight: 750; letter-spacing: .06em; }
.event-window-context strong { font: 700 11px/1.4 var(--mono); }
.event-window-context a { color: #ffd0ad; font-size: 10px; font-weight: 750; text-decoration: none; }

.legacy-boundary {
  display: grid;
  grid-template-columns: minmax(240px, auto) 1fr;
  align-items: center;
  gap: 18px;
  padding: 11px 15px;
  color: #684c31;
  background: #fffaf2;
  border: 1px solid #ebd6aa;
  border-left: 4px solid #df7a1f;
}
.legacy-boundary b { color: #9a4f0c; font: 800 9px/1.3 var(--mono); letter-spacing: .06em; }
.legacy-boundary p { margin: 0; font-size: 10px; line-height: 1.55; }

.asn-console {
  display: grid;
  grid-template-columns: minmax(220px, 340px) 132px auto 1fr;
  align-items: end;
  gap: 12px;
  padding: 14px 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.asn-console label { display: grid; gap: 7px; }
.asn-console label span,
.console-freshness {
  color: var(--muted);
  font: 650 9px/1.3 var(--mono);
  letter-spacing: .035em;
  text-transform: uppercase;
}
.asn-console input {
  width: 100%;
  height: 38px;
  padding: 0 11px;
  color: var(--ink);
  background: #fff;
  border: 1px solid var(--line-dark);
  border-radius: 5px;
}
.asn-console .solid-action { min-height: 38px; }
.text-action { align-self: center; color: var(--primary); font-size: 11px; font-weight: 700; text-decoration: none; }
.console-freshness { align-self: center; justify-self: end; }

.scope-note {
  display: grid;
  grid-template-columns: 190px 1fr;
  align-items: center;
  gap: 18px;
  padding: 12px 16px;
  color: #334155;
  background: #f4f7fb;
  border: 1px solid #cbd5e1;
  border-left: 3px solid var(--primary);
}
.scope-note div { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.scope-note span { color: var(--primary); font: 700 9px/1 var(--mono); letter-spacing: .05em; }
.scope-note strong { font: 750 17px/1 var(--mono); white-space: nowrap; }
.scope-note p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.5; }

.asn-leaders {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}
.asn-leaders article { min-width: 0; display: grid; gap: 7px; padding: 17px 18px; }
.asn-leaders article + article { border-left: 1px solid var(--line); }
.asn-leaders span,
.dossier-metrics span { color: var(--muted); font-size: 9px; font-weight: 700; letter-spacing: .035em; text-transform: uppercase; }
.asn-leaders strong { overflow: hidden; color: #17212b; font-size: clamp(22px, 2.2vw, 30px); letter-spacing: -.04em; text-overflow: ellipsis; white-space: nowrap; }
.asn-leaders b { color: #536171; font: 650 10px/1.3 var(--mono); }

.ranking-board { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.ranking-sheet { overflow: hidden; background: var(--paper); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.ranking-sheet:last-child { grid-column: 1 / -1; }
.ranking-sheet > header { display: grid; grid-template-columns: 36px 1fr; align-items: center; gap: 12px; min-height: 70px; padding: 13px 16px; border-bottom: 1px solid var(--line); }
.ranking-sheet > header > span { color: var(--signal); font: 750 13px/1 var(--mono); }
.ranking-sheet h2,
.ranking-sheet p { margin: 0; }
.ranking-sheet h2 { color: #24313d; font-size: 16px; }
.ranking-sheet p { margin-top: 4px; color: var(--muted); font: 600 8px/1 var(--mono); letter-spacing: .045em; }
.ranking-sheet ol { margin: 0; padding: 0; list-style: none; }
.ranking-sheet li { min-height: 64px; display: grid; grid-template-columns: 28px minmax(130px, 1fr) 132px minmax(82px, auto); align-items: center; gap: 10px; padding: 10px 16px; }
.ranking-sheet li + li { border-top: 1px solid #edf0f4; }
.rank-index { color: #98a2b3; font: 650 9px/1 var(--mono); }
.ranking-sheet li > a { min-width: 0; display: grid; gap: 4px; text-decoration: none; }
.ranking-sheet li > a:hover strong { color: var(--primary); }
.ranking-sheet li > a strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.ranking-sheet li > a small { overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.ranking-sheet em { margin-left: 5px; padding: 2px 4px; color: #9a3412; background: #ffedd5; border-radius: 3px; font: 700 7px/1 var(--mono); font-style: normal; }
.ranking-sheet li > b { justify-self: end; color: #17212b; font: 720 13px/1 var(--mono); white-space: nowrap; }
.ranking-sheet li > b small { color: var(--muted); font-size: 8px; }

.asn-dossier { overflow: hidden; background: var(--paper); border: 1px solid var(--line); border-top: 3px solid var(--primary); border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.dossier-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 20px; border-bottom: 1px solid var(--line); }
.dossier-heading p,
.dossier-heading h2 { margin: 0; }
.dossier-heading p { color: var(--primary); font: 700 9px/1 var(--mono); letter-spacing: .05em; }
.dossier-heading h2 { margin-top: 7px; color: #17212b; font-size: 27px; letter-spacing: -.035em; }
.dossier-heading > div > span { display: block; margin-top: 7px; color: var(--muted); font-size: 10px; }
.dossier-actions { display: grid; justify-items: end; gap: 7px; font-size: 10px; }
.dossier-actions a { color: var(--primary); font-weight: 700; text-decoration: none; }
.dossier-actions span { color: var(--muted); font-family: var(--mono); }
.dossier-actions b { padding: 4px 6px; color: #9a3412; background: #ffedd5; font: 700 8px/1 var(--mono); }
.identity-ledger { display: flex; flex-wrap: wrap; gap: 1px; background: var(--line); border-bottom: 1px solid var(--line); }
.identity-ledger span { flex: 1 1 180px; padding: 9px 14px; color: var(--muted); background: #f8fafc; font: 600 8px/1.3 var(--mono); }
.identity-ledger b { color: #334155; }
.dossier-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.dossier-metrics article { min-width: 0; display: grid; align-content: start; gap: 8px; padding: 15px 14px; }
.dossier-metrics article + article { border-left: 1px solid var(--line); }
.dossier-metrics article:nth-child(n + 5) { border-top: 1px solid var(--line); }
.dossier-metrics article:nth-child(5) { border-left: 0; }
.dossier-metrics strong { overflow: hidden; color: #17212b; font: 740 clamp(17px, 1.8vw, 23px)/1 var(--mono); letter-spacing: -.04em; text-overflow: ellipsis; white-space: nowrap; }
.dossier-metrics small { color: var(--muted); font-size: 8px; line-height: 1.45; }
.asn-chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; background: var(--line); border-bottom: 1px solid var(--line); }
.asn-chart-panel { min-width: 0; padding: 18px; background: var(--paper); }
.asn-chart-panel.is-wide { grid-column: 1 / -1; }
.asn-chart-panel .section-heading,
.asn-events .section-heading { margin: 0 0 14px; }
.asn-chart-panel h3,
.asn-events h3 { margin: 0; color: #24313d; font-size: 15px; }
.asn-events { padding: 18px; }

@media (max-width: 1180px) {
  .asn-console { grid-template-columns: minmax(220px, 1fr) 132px auto; }
  .console-freshness { grid-column: 1 / -1; justify-self: start; }
  .ranking-sheet li { grid-template-columns: 26px minmax(100px, 1fr) minmax(76px, auto); }
  .ranking-sheet :deep(.sparkline-pair) { display: none; }
}

@media (max-width: 820px) {
  .scope-note,
  .asn-leaders,
  .ranking-board,
  .asn-chart-grid { grid-template-columns: 1fr; }
  .ranking-sheet:last-child { grid-column: auto; }
  .asn-leaders article + article { border-top: 1px solid var(--line); border-left: 0; }
  .asn-chart-panel.is-wide { grid-column: auto; }
  .dossier-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dossier-metrics article:nth-child(odd) { border-left: 0; }
  .dossier-metrics article:nth-child(n + 3) { border-top: 1px solid var(--line); }
}

@media (max-width: 620px) {
  .legacy-boundary { grid-template-columns: 1fr; gap: 5px; }
  .asn-console { grid-template-columns: 1fr; }
  .text-action,
  .console-freshness { justify-self: start; }
  .ranking-sheet li { grid-template-columns: 24px minmax(0, 1fr) auto; padding-inline: 12px; }
  .dossier-heading { align-items: start; flex-direction: column; }
  .dossier-actions { justify-items: start; }
  .dossier-metrics { grid-template-columns: 1fr; }
  .dossier-metrics article + article { border-top: 1px solid var(--line); border-left: 0; }
}
</style>
