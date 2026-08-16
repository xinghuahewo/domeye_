<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getEvents } from '@/api/events'
import {
  getCountryASOutages,
  getCountryOverview,
  getCountryPrefixOutages,
  type FeatureRange,
} from '@/api/features'
import EventTable from '@/components/EventTable.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import SparklinePair from '@/components/SparklinePair.vue'
import type { CountryOverview, CountryProfile, EventRow, OutagePoint } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { recentRange, toBackendTime } from '@/utils/time'

const route = useRoute()
const router = useRouter()
const defaults = recentRange(24)
const query = reactive({ start: defaults.start, end: defaults.end })
const countryInput = ref('')
const overview = ref<CountryOverview | null>(null)
const asOutages = ref<OutagePoint[]>([])
const prefixOutages = ref<OutagePoint[]>([])
const recentEvents = ref<EventRow[]>([])
const loading = ref(false)
const error = ref('')
const outageLoading = ref(false)
const outageError = ref('')
const eventsLoading = ref(false)
const eventError = ref('')
let loadToken = 0

const selectedName = computed(() => {
  const value = route.params.country
  return typeof value === 'string' ? value.trim() : ''
})
const selected = computed(() => overview.value?.selectedCountry ?? null)

const countrySuggestions = computed(() => {
  const names = new Set<string>()
  for (const ranking of [
    overview.value?.updateRankings,
    overview.value?.withdrawRateRankings,
    overview.value?.resourceChangeRankings,
    overview.value?.anomalyRankings,
  ]) {
    for (const profile of ranking ?? []) names.add(profile.country)
  }
  return [...names].sort((left, right) => left.localeCompare(right, 'zh-CN'))
})

const rankingSections = computed(() => [
  {
    key: 'updates',
    index: '01',
    title: '更新量最高',
    note: 'ANNOUNCE + WITHDRAW',
    rows: overview.value?.updateRankings ?? [],
    value: (profile: CountryProfile) => formatNumber(profile.updateTotal),
    unit: '条',
  },
  {
    key: 'withdraw',
    index: '02',
    title: '撤回率最高',
    note: 'WITHDRAW / UPDATES',
    rows: overview.value?.withdrawRateRankings ?? [],
    value: (profile: CountryProfile) => `${profile.withdrawRate.toFixed(1)}%`,
    unit: '',
  },
  {
    key: 'resource',
    index: '03',
    title: '资源变化最大',
    note: 'MAX OF /24 OR /48 CHANGE RATE',
    rows: overview.value?.resourceChangeRankings ?? [],
    value: (profile: CountryProfile) => profile.resourceChangeRate === null
      ? '—'
      : `${profile.resourceChangeRate.toFixed(1)}%`,
    unit: '',
  },
  {
    key: 'anomaly',
    index: '04',
    title: '异常事件最多',
    note: 'SIX CORE CLASSES',
    rows: overview.value?.anomalyRankings ?? [],
    value: (profile: CountryProfile) => formatNumber(profile.anomalyCount),
    unit: '起',
  },
])

const messageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: (selected.value?.series ?? []).map((point) => [point.time, point.announce]),
  },
  {
    name: 'WITHDRAW',
    color: '#35b6d4',
    data: (selected.value?.series ?? []).map((point) => [point.time, point.withdraw]),
  },
])

const resourceSeries = computed<ChartSeries[]>(() => [
  {
    name: 'IPv4 /24 SEGMENTS',
    color: '#175cd3',
    data: (selected.value?.series ?? [])
      .filter((point) => point.ipv4Prefixes !== null)
      .map((point) => [point.time, point.ipv4Prefixes]),
  },
  {
    name: 'IPv6 /48 SEGMENTS',
    color: '#35b6d4',
    data: (selected.value?.series ?? [])
      .filter((point) => point.ipv6Prefixes !== null)
      .map((point) => [point.time, point.ipv6Prefixes]),
  },
])

const outageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'AS OUTAGE',
    color: '#f48120',
    data: asOutages.value.map((point) => [point.time, point.count]),
  },
  {
    name: 'PREFIX OUTAGE',
    color: '#c9372c',
    data: prefixOutages.value.map((point) => [point.time, point.count]),
  },
])

const expectedSamples = computed(() => {
  const start = new Date(query.start).getTime()
  const end = new Date(query.end).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0
  return Math.floor((end - start) / 300_000) + 1
})
const sampleCoverage = computed(() => {
  if (!selected.value || expectedSamples.value === 0) return null
  return Math.min(100, selected.value.sampleCount / expectedSamples.value * 100)
})

function featureRange(): FeatureRange {
  return { start_time: toBackendTime(query.start), end_time: toBackendTime(query.end) }
}

function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : value.toLocaleString('zh-CN')
}

function formatDelta(value: number | null) {
  if (value === null) return '—'
  if (value === 0) return '0'
  return `${value > 0 ? '+' : ''}${value.toLocaleString('zh-CN')}`
}

function changeLabel(value: number | null) {
  if (value === null) return '上一窗口无基线'
  if (value === 0) return '与上一窗口持平'
  return `${value > 0 ? '↑' : '↓'} ${Math.abs(value).toFixed(1)}% 较上一窗口`
}

async function load() {
  const token = ++loadToken
  loading.value = true
  error.value = ''
  outageError.value = ''
  eventError.value = ''
  asOutages.value = []
  prefixOutages.value = []
  recentEvents.value = []
  const range = featureRange()
  const country = selectedName.value
  outageLoading.value = Boolean(country)
  eventsLoading.value = Boolean(country)
  let overviewReady = false
  try {
    const result = await getCountryOverview(range, country || undefined, 6)
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
  if (!overviewReady || !country || token !== loadToken) return

  try {
    const result = await getEvents({
      page_num: 1,
      page_size: 10,
      attacked_country: country,
      country: 'all',
      date: `${range.start_time}_${range.end_time}`,
      sort_mode: 'start_timeB',
    })
    if (token === loadToken) recentEvents.value = result.data
  } catch (cause) {
    if (token === loadToken) eventError.value = errorMessage(cause)
  } finally {
    if (token === loadToken) eventsLoading.value = false
  }
  if (token !== loadToken) return
  try {
    const asResult = await getCountryASOutages(country, range)
    if (token === loadToken) asOutages.value = asResult
    if (token === loadToken) {
      const prefixResult = await getCountryPrefixOutages(country, range)
      if (token === loadToken) prefixOutages.value = prefixResult
    }
  } catch (cause) {
    if (token === loadToken) outageError.value = errorMessage(cause)
  } finally {
    if (token === loadToken) outageLoading.value = false
  }
}

function openCountry(country?: string) {
  const target = (country ?? countryInput.value).trim()
  if (!target) return
  void router.push({ name: 'country-detail', params: { country: target } })
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
}

watch(
  [() => route.params.country, () => query.start, () => query.end],
  () => {
    countryInput.value = selectedName.value
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <article class="page country-page">
    <header class="page-heading country-heading">
      <div>
        <p class="eyebrow">国家态势 / Country desk</p>
        <h1>{{ selectedName || '国家路由态势' }}</h1>
      </div>
      <p class="page-heading-copy">
        把国家报文、路由资源等效段和六类异常放进同一个 24 小时窗口；资源缺失保持为空，不以 0 代替未知。
      </p>
    </header>

    <form class="country-console" @submit.prevent="openCountry()">
      <label>
        <span>检索国家</span>
        <input v-model="countryInput" list="country-suggestions" placeholder="例如：中国、印度、美国" />
        <datalist id="country-suggestions">
          <option v-for="country in countrySuggestions" :key="country" :value="country" />
        </datalist>
      </label>
      <button class="solid-action" type="submit">打开国家档案</button>
      <RouterLink v-if="selectedName" class="text-action" :to="{ name: 'countries' }">返回国家总览</RouterLink>
      <span class="console-freshness">
        DATA CUT · {{ overview?.latestObservation || '尚无观测' }}
      </span>
    </form>

    <PageState v-if="loading && !overview" kind="loading" title="正在聚合国家态势" detail="只读查询当前与上一等长窗口" />
    <PageState v-else-if="error" kind="error" title="国家态势不可用" :detail="error" @retry="load" />

    <template v-if="overview">
      <section class="country-leaders" aria-label="国家态势核心指标">
        <article>
          <span>更新量最高</span>
          <strong>{{ overview.updateLeader?.country || '—' }}</strong>
          <b>{{ formatNumber(overview.updateLeader?.updateTotal) }} 条</b>
        </article>
        <article>
          <span>撤回率最高</span>
          <strong>{{ overview.withdrawRateLeader?.country || '—' }}</strong>
          <b>{{ overview.withdrawRateLeader ? `${overview.withdrawRateLeader.withdrawRate.toFixed(1)}%` : '—' }}</b>
        </article>
        <article>
          <span>资源变化最大</span>
          <strong>{{ overview.resourceChangeLeader?.country || '—' }}</strong>
          <b>{{ overview.resourceChangeLeader?.resourceChangeRate === null || overview.resourceChangeLeader?.resourceChangeRate === undefined ? '—' : `${overview.resourceChangeLeader.resourceChangeRate.toFixed(1)}%` }} 最大单栈变化</b>
        </article>
        <article>
          <span>存在异常</span>
          <strong>{{ overview.countriesWithAnomalies }}</strong>
          <b>/ {{ overview.countryCount }} 个有特征国家</b>
        </article>
      </section>

      <section class="ranking-board" aria-label="国家排行">
        <article v-for="section in rankingSections" :key="section.key" class="ranking-sheet">
          <header>
            <span>{{ section.index }}</span>
            <div>
              <h2>{{ section.title }}</h2>
              <p>{{ section.note }}</p>
            </div>
          </header>
          <ol>
            <li v-for="(profile, index) in section.rows" :key="profile.country">
              <span class="rank-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <RouterLink :to="{ name: 'country-detail', params: { country: profile.country } }">
                <strong>{{ profile.country }}</strong>
                <small>{{ profile.anomalyCount }} 异常 · {{ profile.highRiskCount }} 高风险</small>
              </RouterLink>
              <SparklinePair :points="profile.sparkline" :label="`${profile.country} 报文趋势`" />
              <b>{{ section.value(profile) }} <small>{{ section.unit }}</small></b>
            </li>
          </ol>
        </article>
      </section>

      <section v-if="selected" class="country-dossier" aria-labelledby="country-dossier-title">
        <header class="dossier-heading">
          <div>
            <p>SELECTED COUNTRY / 24H DOSSIER</p>
            <h2 id="country-dossier-title">{{ selected.country }}</h2>
          </div>
          <div class="dossier-actions">
            <RouterLink :to="{ name: 'events', query: { attacked_country: selected.country } }">
              检索该国家事件 →
            </RouterLink>
            <span>最后观测 {{ selected.latestObservation || '未知' }}</span>
          </div>
        </header>

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
            <small>{{ formatDelta(selected.ipv4PrefixChange) }} 较窗口基线</small>
          </article>
          <article>
            <span>IPv6 /48 等效段</span>
            <strong>{{ formatNumber(selected.ipv6Prefixes) }}</strong>
            <small>{{ formatDelta(selected.ipv6PrefixChange) }} 较窗口基线</small>
          </article>
          <article>
            <span>窗口峰值</span>
            <strong>{{ formatNumber(selected.peakUpdates) }}</strong>
            <small>{{ selected.peakTime || '峰值时间未知' }}</small>
          </article>
          <article>
            <span>异常 / 高风险</span>
            <strong>{{ selected.anomalyCount }} / {{ selected.highRiskCount }}</strong>
            <small>六类核心异常</small>
          </article>
          <article>
            <span>样本覆盖</span>
            <strong>{{ sampleCoverage === null ? '—' : `${sampleCoverage.toFixed(1)}%` }}</strong>
            <small>{{ selected.sampleCount }} / {{ expectedSamples }} 预期样本</small>
          </article>
        </div>

        <div class="country-chart-grid">
          <section class="country-chart-panel">
            <div class="section-heading">
              <h3>报文脉冲</h3>
              <span>announce / withdraw</span>
            </div>
            <PageState v-if="selected.series.length === 0" title="当前窗口没有国家报文样本" />
            <LineChart v-else :series="messageSeries" unit="条" :height="300" />
          </section>
          <section class="country-chart-panel">
            <div class="section-heading">
              <h3>路由资源等效段</h3>
              <span>null ≠ zero</span>
            </div>
            <PageState v-if="selected.series.length === 0" title="当前窗口没有资源快照" />
            <LineChart v-else :series="resourceSeries" unit="个" :height="300" />
          </section>
          <section class="country-chart-panel is-wide">
            <div class="section-heading">
              <h3>并发中断</h3>
              <span>3-minute active slots</span>
            </div>
            <PageState v-if="outageLoading" kind="loading" title="正在读取国家中断时间槽" />
            <PageState v-else-if="outageError" kind="error" title="国家中断时序不可用" :detail="outageError" @retry="load" />
            <LineChart v-else :series="outageSeries" unit="起" :height="270" />
          </section>
        </div>

        <section class="country-events">
          <div class="section-heading">
            <h3>最近异常事件</h3>
            <span>{{ recentEvents.length }} latest records</span>
          </div>
          <PageState v-if="eventsLoading" kind="loading" title="正在读取国家最近事件" />
          <PageState v-else-if="eventError" kind="error" title="国家事件不可用" :detail="eventError" @retry="load" />
          <PageState v-else-if="recentEvents.length === 0" title="当前窗口没有可展示的国家异常事件" />
          <EventTable v-else compact :events="recentEvents" @select="openEvent" />
        </section>
      </section>

      <PageState
        v-else
        title="从排行打开一个国家档案"
        detail="国家总览保留四个定位入口，详情页再加载单国时序和最近事件。"
      />
    </template>
  </article>
</template>

<style scoped>
.country-heading {
  align-items: end;
}

.country-console {
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

.country-console label {
  display: grid;
  gap: 7px;
}

.country-console label span,
.console-freshness {
  color: var(--muted);
  font: 650 9px/1.3 var(--mono);
  letter-spacing: 0.035em;
  text-transform: uppercase;
}

.country-console input {
  width: 100%;
  height: 38px;
  padding: 0 11px;
  color: var(--ink);
  background: #fff;
  border: 1px solid var(--line-dark);
  border-radius: 5px;
}

.country-console .solid-action {
  min-height: 38px;
}

.text-action {
  align-self: center;
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
}

.console-freshness {
  align-self: center;
  justify-self: end;
}

.country-leaders {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.country-leaders article {
  min-width: 0;
  display: grid;
  gap: 7px;
  padding: 17px 18px;
}

.country-leaders article + article {
  border-left: 1px solid var(--line);
}

.country-leaders span,
.dossier-metrics span {
  color: var(--muted);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.035em;
  text-transform: uppercase;
}

.country-leaders strong {
  overflow: hidden;
  color: #17212b;
  font-size: clamp(22px, 2.3vw, 31px);
  font-weight: 760;
  letter-spacing: -0.04em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.country-leaders b {
  color: #536171;
  font: 650 10px/1.3 var(--mono);
}

.ranking-board {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.ranking-sheet {
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.ranking-sheet > header {
  display: grid;
  grid-template-columns: 36px 1fr;
  align-items: center;
  gap: 12px;
  min-height: 70px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--line);
}

.ranking-sheet > header > span {
  color: var(--signal);
  font: 750 13px/1 var(--mono);
}

.ranking-sheet h2,
.ranking-sheet p {
  margin: 0;
}

.ranking-sheet h2 {
  color: #24313d;
  font-size: 16px;
}

.ranking-sheet p {
  margin-top: 4px;
  color: var(--muted);
  font: 600 8px/1 var(--mono);
  letter-spacing: 0.045em;
}

.ranking-sheet ol {
  margin: 0;
  padding: 0;
  list-style: none;
}

.ranking-sheet li {
  min-height: 64px;
  display: grid;
  grid-template-columns: 28px minmax(105px, 1fr) 132px minmax(82px, auto);
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
}

.ranking-sheet li + li {
  border-top: 1px solid #edf0f4;
}

.rank-index {
  color: #98a2b3;
  font: 650 9px/1 var(--mono);
}

.ranking-sheet li > a {
  min-width: 0;
  display: grid;
  gap: 4px;
  text-decoration: none;
}

.ranking-sheet li > a:hover strong {
  color: var(--primary);
}

.ranking-sheet li > a strong {
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ranking-sheet li > a small {
  color: var(--muted);
  font-size: 9px;
}

.ranking-sheet li > b {
  justify-self: end;
  color: #17212b;
  font: 720 13px/1 var(--mono);
  white-space: nowrap;
}

.ranking-sheet li > b small {
  color: var(--muted);
  font-size: 8px;
}

.country-dossier {
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-top: 3px solid var(--primary);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.dossier-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  padding: 20px;
  border-bottom: 1px solid var(--line);
}

.dossier-heading p,
.dossier-heading h2 {
  margin: 0;
}

.dossier-heading p {
  color: var(--primary);
  font: 700 9px/1 var(--mono);
  letter-spacing: 0.05em;
}

.dossier-heading h2 {
  margin-top: 7px;
  color: #17212b;
  font-size: 30px;
  letter-spacing: -0.035em;
}

.dossier-actions {
  display: grid;
  justify-items: end;
  gap: 7px;
  font-size: 10px;
}

.dossier-actions a {
  color: var(--primary);
  font-weight: 700;
  text-decoration: none;
}

.dossier-actions span {
  color: var(--muted);
  font-family: var(--mono);
}

.dossier-metrics {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  border-bottom: 1px solid var(--line);
}

.dossier-metrics article {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 15px 14px;
}

.dossier-metrics article + article {
  border-left: 1px solid var(--line);
}

.dossier-metrics strong {
  overflow: hidden;
  color: #17212b;
  font: 740 clamp(17px, 1.8vw, 23px)/1 var(--mono);
  letter-spacing: -0.04em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dossier-metrics small {
  color: var(--muted);
  font-size: 8px;
  line-height: 1.45;
}

.country-chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  background: var(--line);
  border-bottom: 1px solid var(--line);
}

.country-chart-panel {
  min-width: 0;
  padding: 18px;
  background: var(--paper);
}

.country-chart-panel.is-wide {
  grid-column: 1 / -1;
}

.country-chart-panel .section-heading,
.country-events .section-heading {
  margin: 0 0 14px;
}

.country-chart-panel h3,
.country-events h3 {
  margin: 0;
  color: #24313d;
  font-size: 15px;
}

.country-events {
  padding: 18px;
}

@media (max-width: 1180px) {
  .country-console {
    grid-template-columns: minmax(220px, 1fr) 132px auto;
  }

  .console-freshness {
    grid-column: 1 / -1;
    justify-self: start;
  }

  .ranking-sheet li {
    grid-template-columns: 26px minmax(100px, 1fr) minmax(76px, auto);
  }

  .ranking-sheet :deep(.sparkline-pair) {
    display: none;
  }

  .dossier-metrics {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .dossier-metrics article:nth-child(5) {
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .dossier-metrics article:nth-child(n + 6) {
    border-top: 1px solid var(--line);
  }
}

@media (max-width: 820px) {
  .country-leaders,
  .ranking-board,
  .country-chart-grid {
    grid-template-columns: 1fr;
  }

  .country-leaders article + article {
    border-top: 1px solid var(--line);
    border-left: 0;
  }

  .country-chart-panel.is-wide {
    grid-column: auto;
  }

  .dossier-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dossier-metrics article:nth-child(odd) {
    border-left: 0;
  }

  .dossier-metrics article:nth-child(n + 3) {
    border-top: 1px solid var(--line);
  }
}

@media (max-width: 620px) {
  .country-console {
    grid-template-columns: 1fr;
  }

  .text-action,
  .console-freshness {
    justify-self: start;
  }

  .ranking-sheet li {
    grid-template-columns: 24px minmax(0, 1fr) auto;
    padding-inline: 12px;
  }

  .dossier-heading {
    align-items: start;
    flex-direction: column;
  }

  .dossier-actions {
    justify-items: start;
  }

  .dossier-metrics {
    grid-template-columns: 1fr;
  }

  .dossier-metrics article + article {
    border-top: 1px solid var(--line);
    border-left: 0;
  }

  .country-chart-panel,
  .country-events {
    padding: 14px;
  }
}
</style>
