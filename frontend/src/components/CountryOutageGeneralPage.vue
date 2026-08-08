<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  getCountryOutageGeneralAffectedAs,
  getCountryOutageGeneralPathDownstreams,
} from '@/api/events'
import ObservationChart, { type ObservationChartSeries } from '@/components/ObservationChart.vue'
import PageState from '@/components/PageState.vue'
import type {
  CountryOutageGeneralAffectedAsPage,
  CountryOutageGeneralPageModel,
  CountryOutageGeneralPathDownstreamPage,
  CountryOutageGeneralTrackKey,
} from '@/types/api'
import { errorMessage } from '@/utils/normalize'

defineOptions({ name: 'CountryOutageGeneralPage' })

const props = defineProps<{
  page: CountryOutageGeneralPageModel
  reference: string
}>()

const route = useRoute()
const asPageSize = 20
const pathPageSize = 15
const asResult = ref<CountryOutageGeneralAffectedAsPage | null>(null)
const asLoading = ref(false)
const asError = ref('')
const asPage = ref(readPositiveRouteNumber('as_page', 1))
const asQuery = ref(readRouteText('as_query'))
const asClassification = ref<'all' | 'affected' | 'route_interrupted'>(
  readRouteChoice('as_classification', ['all', 'affected', 'route_interrupted'], 'all'),
)
const pathResult = ref<CountryOutageGeneralPathDownstreamPage | null>(null)
const pathLoading = ref(false)
const pathError = ref('')
const pathPage = ref(1)
const pathQuery = ref('')
const pathAffectedAsn = ref('')
const pathScope = ref<'all' | 'concurrent'>('all')
let asRequest = 0
let pathRequest = 0

function readRouteText(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

function readPositiveRouteNumber(key: string, fallback: number): number {
  const value = Number(readRouteText(key))
  return Number.isInteger(value) && value > 0 ? value : fallback
}

function readRouteChoice<T extends string>(key: string, choices: readonly T[], fallback: T): T {
  const value = readRouteText(key)
  return choices.includes(value as T) ? value as T : fallback
}

const countryName = computed(() => {
  try {
    return new Intl.DisplayNames(['zh-CN'], { type: 'region' })
      .of(props.page.resolution.country_code) || props.page.resolution.country_code
  } catch {
    return props.page.resolution.country_code
  }
})

function formatNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function track(key: CountryOutageGeneralTrackKey): number[] {
  return props.page.series.tracks[key]
}

function chartSeries(
  items: Array<{ key: CountryOutageGeneralTrackKey, name: string, color: string, area?: boolean }>,
): ObservationChartSeries[] {
  return items.map((item) => ({
    name: item.name,
    color: item.color,
    area: item.area,
    data: props.page.series.timestamps.map((timestamp, index) => [
      timestamp,
      track(item.key)[index] ?? null,
    ]),
  }))
}

const prefixSeries = computed(() => chartSeries([
  { key: 'interrupted_prefix_count', name: '出现不可见的固定前缀', color: '#e2652a', area: true },
  { key: 'completely_interrupted_prefix_count', name: '所有观察方向均不可见', color: '#712f2a' },
]))

const asSeries = computed(() => chartSeries([
  { key: 'affected_asn_count', name: '出现不可见前缀的 AS', color: '#196b8a', area: true },
  { key: 'route_interrupted_asn_count', name: '固定前缀均不可见的 AS', color: '#102f46' },
]))

const ipv4Series = computed(() => chartSeries([
  { key: 'fixed_visible_ipv4_address_count', name: '固定前缀可见 IPv4 地址', color: '#176d8f', area: true },
  { key: 'new_visible_ipv4_address_count', name: '新出现前缀当前可见地址', color: '#da762d' },
]))

const ipv6Series = computed(() => chartSeries([
  { key: 'fixed_visible_ipv6_slash48_count', name: '固定前缀可见 /48 等价块', color: '#5b54a4', area: true },
  { key: 'new_visible_ipv6_slash48_count', name: '新出现前缀当前可见 /48 等价块', color: '#bd70a2' },
]))

const overview = computed(() => props.page.overview)
const peakPrefix = computed(() => overview.value.peaks.interrupted_prefix_count)
const peakIndex = computed(() => peakPrefix.value
  ? props.page.series.timestamps.indexOf(peakPrefix.value.state_point_utc)
  : -1)
function atPeak(key: CountryOutageGeneralTrackKey): number | null {
  return peakIndex.value >= 0 ? track(key)[peakIndex.value] ?? null : null
}

const reading = computed(() => {
  if (!peakPrefix.value) return '当前窗口没有可用的前缀不可见峰值。'
  const complete = atPeak('completely_interrupted_prefix_count')
  const directions = atPeak('invisible_direction_count')
  return `${formatTime(peakPrefix.value.state_point_utc)}，${formatNumber(peakPrefix.value.value)} 个固定前缀在至少一个独立观察方向不可见；其中 ${formatNumber(complete)} 个在所有观察方向均不可见，共涉及 ${formatNumber(directions)} 条不可见观察方向。`
})

const lifecycleText = computed(() => props.page.resolution.is_final_in_data_range
  ? '该事件在当前数据范围内已结束'
  : '观测持续到当前数据范围末端，尚不能判定事件结束')

async function loadAffectedAs() {
  const token = ++asRequest
  asLoading.value = true
  asError.value = ''
  try {
    const result = await getCountryOutageGeneralAffectedAs(props.page.resolution, {
      page: asPage.value,
      page_size: asPageSize,
      query: asQuery.value.trim() || undefined,
      classification: asClassification.value,
      sort: 'default',
    })
    if (token === asRequest) asResult.value = result
  } catch (cause) {
    if (token === asRequest) {
      asResult.value = null
      asError.value = errorMessage(cause)
    }
  } finally {
    if (token === asRequest) asLoading.value = false
  }
}

function applyAsFilters() {
  asPage.value = 1
  void loadAffectedAs()
}

function changeAsPage(page: number) {
  if (page < 1 || page > (asResult.value?.page_count ?? 1)) return
  asPage.value = page
  void loadAffectedAs()
}

function parseAffectedAsn(): number | undefined {
  const value = pathAffectedAsn.value.trim().replace(/^AS/i, '')
  return /^\d+$/.test(value) ? Number(value) : undefined
}

async function loadPaths() {
  const affectedAsn = parseAffectedAsn()
  if (pathAffectedAsn.value.trim() && affectedAsn === undefined) {
    pathError.value = '请输入纯数字 ASN 或 AS 加数字'
    return
  }
  const token = ++pathRequest
  pathLoading.value = true
  pathError.value = ''
  try {
    const result = await getCountryOutageGeneralPathDownstreams(props.page.resolution, {
      page: pathPage.value,
      page_size: pathPageSize,
      affected_asn: affectedAsn,
      scope: pathScope.value,
      query: pathQuery.value.trim() || undefined,
    })
    if (token === pathRequest) pathResult.value = result
  } catch (cause) {
    if (token === pathRequest) {
      pathResult.value = null
      pathError.value = errorMessage(cause)
    }
  } finally {
    if (token === pathRequest) pathLoading.value = false
  }
}

function applyPathFilters() {
  pathPage.value = 1
  void loadPaths()
}

function changePathPage(page: number) {
  if (page < 1 || page > (pathResult.value?.page_count ?? 1)) return
  pathPage.value = page
  void loadPaths()
}

function classificationLabel(value: string): string {
  return value === 'route_interrupted' ? '固定前缀均不可见' : '部分固定前缀不可见'
}

function asProfileLink(asn: number) {
  return {
    name: 'asn-detail',
    params: { asn: String(asn) },
    query: {
      event_start: props.page.resolution.window_start_utc,
      event_end: props.page.resolution.window_end_utc,
      event_ref: props.reference,
      return_anchor: 'affected-as',
      as_page: String(asPage.value),
      as_query: asQuery.value || undefined,
      as_classification: asClassification.value,
    },
  }
}

const chatLink = computed(() => ({
  name: 'country-outage-chat',
  query: {
    ref: props.reference,
    publication_id: props.page.resolution.publication_id,
    revision: String(props.page.resolution.revision),
    incident_id: props.page.resolution.incident_id,
    collector_id: props.page.resolution.collector_id,
    data_through: props.page.resolution.data_through || undefined,
    final: String(props.page.resolution.is_final_in_data_range),
    country: props.page.resolution.country_code,
  },
}))

onMounted(() => {
  void loadAffectedAs()
  void loadPaths()
  if (readRouteText('focus')) {
    void nextTick(() => document.getElementById(readRouteText('focus'))?.scrollIntoView())
  }
})
</script>

<template>
  <article class="general-page">
    <header class="event-hero">
      <div class="hero-copy">
        <RouterLink class="back-link" to="/events">← 返回异常事件</RouterLink>
        <p>国家路由可见性观测</p>
        <h1>{{ countryName }}网络中断事件</h1>
        <strong>{{ lifecycleText }}</strong>
        <RouterLink class="chat-entry" :to="chatLink">
          <span>EVENT-BOUND Q&amp;A</span>
          围绕此事件提问 <b>→</b>
        </RouterLink>
      </div>
      <dl class="event-window">
        <div><dt>检测时间</dt><dd>{{ formatTime(overview.event.detected_at_utc) }}</dd></div>
        <div><dt>观测窗口</dt><dd>{{ formatTime(page.resolution.window_start_utc) }} — {{ formatTime(page.resolution.window_end_utc) }}</dd></div>
      </dl>
    </header>

    <section class="reading-card" aria-labelledby="event-reading-title">
      <div>
        <span>本次观测最值得注意的时点</span>
        <h2 id="event-reading-title">路由不可见的集中变化</h2>
        <p>{{ reading }}</p>
      </div>
      <dl>
        <div><dt>固定前缀</dt><dd>{{ formatNumber(overview.cohort.fixed_prefix_count) }}</dd></div>
        <div><dt>相关 AS</dt><dd>{{ formatNumber(overview.affected_as_count) }}</dd></div>
        <div><dt>实际路径关联</dt><dd>{{ formatNumber(overview.path_downstream_relation_count) }}</dd></div>
      </dl>
    </section>

    <section class="sheet" aria-labelledby="prefix-trend-title">
      <header class="section-heading">
        <div><span>01</span><h2 id="prefix-trend-title">前缀中断数量变化</h2></div>
        <p>只要某个固定前缀在至少一个独立观察方向看不到路由，就计入中断。</p>
      </header>
      <ObservationChart :series="prefixSeries" unit="个前缀" :height="330" />
      <div class="inline-facts">
        <p><span>中断前缀峰值</span><b>{{ formatNumber(overview.peaks.interrupted_prefix_count?.value) }}</b></p>
        <p><span>完全不可见峰值</span><b>{{ formatNumber(overview.peaks.completely_interrupted_prefix_count?.value) }}</b></p>
        <p><span>不可见观察方向峰值</span><b>{{ formatNumber(overview.peaks.invisible_direction_count?.value) }}</b></p>
      </div>
    </section>

    <section class="sheet" aria-labelledby="as-trend-title">
      <header class="section-heading">
        <div><span>02</span><h2 id="as-trend-title">AS 中断数量变化</h2></div>
        <p>区分出现部分不可见前缀的 AS，与固定前缀全部不可见的 AS。</p>
      </header>
      <ObservationChart :series="asSeries" unit="个 AS" :height="320" />
    </section>

    <section class="sheet" aria-labelledby="ip-trend-title">
      <header class="section-heading">
        <div><span>03</span><h2 id="ip-trend-title">IP 地址变化趋势</h2></div>
        <p>固定前缀与事件后新出现的前缀分开计算，避免新路由掩盖原有网络的变化。</p>
      </header>
      <div class="ip-grid">
        <figure>
          <figcaption><b>IPv4 可见地址</b><span>地址数</span></figcaption>
          <ObservationChart :series="ipv4Series" unit="个地址" :height="285" />
          <p>窗口内累计新出现 {{ formatNumber(overview.current.new_cumulative_ipv4_prefix_count) }} 个 IPv4 前缀。</p>
        </figure>
        <figure>
          <figcaption><b>IPv6 可见地址规模</b><span>/48 等价块</span></figcaption>
          <ObservationChart :series="ipv6Series" unit="个 /48 等价块" :height="285" />
          <p>窗口内累计新出现 {{ formatNumber(overview.current.new_cumulative_ipv6_prefix_count) }} 个 IPv6 前缀。</p>
        </figure>
      </div>
    </section>

    <section id="affected-as" class="sheet" aria-labelledby="affected-as-title">
      <header class="section-heading">
        <div><span>04</span><h2 id="affected-as-title">哪些 AS 出现了路由不可见</h2></div>
        <p>点击 AS 可在同一事件窗口查看其特征详情。</p>
      </header>
      <form class="filter-bar" @submit.prevent="applyAsFilters">
        <label><span>查找 AS</span><input v-model="asQuery" placeholder="ASN、名称或机构" /></label>
        <label><span>不可见程度</span><select v-model="asClassification"><option value="all">全部</option><option value="affected">部分前缀不可见</option><option value="route_interrupted">固定前缀均不可见</option></select></label>
        <button type="submit">筛选</button>
      </form>
      <PageState v-if="asLoading" kind="loading" title="正在读取相关 AS" />
      <PageState v-else-if="asError" kind="error" title="相关 AS 暂不可用" :detail="asError" @retry="loadAffectedAs" />
      <PageState v-else-if="!asResult?.items.length" title="当前条件下没有相关 AS" />
      <div v-else class="table-scroll">
        <table>
          <thead><tr><th>AS 与性质</th><th>不可见程度</th><th>固定前缀</th><th>部分不可见峰值</th><th>完全不可见峰值</th><th>不可见独立方向峰值</th><th>关联网络</th></tr></thead>
          <tbody>
            <tr v-for="item in asResult.items" :key="item.asn">
              <td><RouterLink :to="asProfileLink(item.asn)">AS{{ item.asn }} →</RouterLink><b>{{ item.as_name || item.organization || '名称未知' }}</b><small>{{ item.nature || '性质未知' }}</small></td>
              <td><em :class="item.event_classification">{{ classificationLabel(item.event_classification) }}</em></td>
              <td>{{ formatNumber(item.fixed_prefix_count) }}</td>
              <td>{{ formatNumber(item.peak_partial_prefix_count) }}</td>
              <td>{{ formatNumber(item.peak_complete_prefix_count) }}</td>
              <td>{{ formatNumber(item.peak_invisible_direction_count) }}</td>
              <td>{{ formatNumber(item.path_downstream_asn_count) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav v-if="asResult && asResult.page_count > 1" class="pager" aria-label="相关 AS 分页">
        <button type="button" :disabled="asPage <= 1" @click="changeAsPage(asPage - 1)">← 上一页</button>
        <span>第 {{ asPage }} / {{ asResult.page_count }} 页 · 共 {{ asResult.total }} 个</span>
        <button type="button" :disabled="asPage >= asResult.page_count" @click="changeAsPage(asPage + 1)">下一页 →</button>
      </nav>
    </section>

    <section class="sheet" aria-labelledby="path-title">
      <header class="section-heading">
        <div><span>05</span><h2 id="path-title">实际路径中关联了哪些网络</h2></div>
        <p>这里展示观测路径中与受影响 AS 相邻出现的网络，以及可核对的路径样本。</p>
      </header>
      <form class="filter-bar is-path" @submit.prevent="applyPathFilters">
        <label><span>受影响 AS</span><input v-model="pathAffectedAsn" placeholder="例如 AS48159" /></label>
        <label><span>查找关联网络</span><input v-model="pathQuery" placeholder="ASN、名称或机构" /></label>
        <label><span>出现时机</span><select v-model="pathScope"><option value="all">窗口内全部</option><option value="concurrent">与中断同期出现</option></select></label>
        <button type="submit">筛选</button>
      </form>
      <PageState v-if="pathLoading" kind="loading" title="正在读取实际路径关联" />
      <PageState v-else-if="pathError" kind="error" title="路径关联暂不可用" :detail="pathError" @retry="loadPaths" />
      <PageState v-else-if="!pathResult?.items.length" title="当前条件下没有路径关联" />
      <div v-else class="relation-list">
        <article v-for="(item, index) in pathResult.items" :key="`${item.affected_asn}-${item.downstream_asn}`">
          <div class="relation-index">{{ String((pathPage - 1) * pathPageSize + index + 1).padStart(2, '0') }}</div>
          <div class="relation-main">
            <h3>AS{{ item.affected_asn }} <span>路径关联</span> AS{{ item.downstream_asn }}</h3>
            <p>{{ item.downstream_as_name || item.downstream_organization || '名称未知' }} · {{ item.downstream_nature || '性质未知' }}</p>
          </div>
          <dl>
            <div><dt>关联固定前缀</dt><dd>{{ formatNumber(item.associated_fixed_prefix_count) }}</dd></div>
            <div><dt>独立观察方向</dt><dd>{{ formatNumber(item.independent_direction_count) }}</dd></div>
            <div><dt>同期状态点</dt><dd>{{ formatNumber(item.concurrent_state_point_count) }}</dd></div>
            <div><dt>同期中断前缀峰值</dt><dd>{{ formatNumber(item.peak_concurrent_interrupted_prefix_count) }}</dd></div>
            <div><dt>同期 IPv4 地址量峰值</dt><dd>{{ formatNumber(item.peak_concurrent_ipv4_address_count) }}</dd></div>
            <div><dt>同期 IPv6 /48 峰值</dt><dd>{{ formatNumber(item.peak_concurrent_ipv6_slash48_count) }}</dd></div>
          </dl>
          <details>
            <summary>查看关联路径</summary>
            <ol>
              <li v-for="sample in item.path_samples" :key="sample.as_path_id">
                <b>{{ sample.prefix }}</b><code>{{ sample.as_path_canonical }}</code><small>{{ sample.independent_peer_asns.length }} 个独立观察方向 · {{ sample.route_observation_count }} 条观测</small>
              </li>
            </ol>
          </details>
        </article>
      </div>
      <nav v-if="pathResult && pathResult.page_count > 1" class="pager" aria-label="路径关联分页">
        <button type="button" :disabled="pathPage <= 1" @click="changePathPage(pathPage - 1)">← 上一页</button>
        <span>第 {{ pathPage }} / {{ pathResult.page_count }} 页 · 共 {{ pathResult.total }} 组</span>
        <button type="button" :disabled="pathPage >= pathResult.page_count" @click="changePathPage(pathPage + 1)">下一页 →</button>
      </nav>
    </section>
  </article>
</template>

<style scoped>
.general-page { --navy: #122b3b; --blue: #176d8f; --orange: #df6b2d; --cream: #f6f2ea; min-width: 0; width: 100%; display: grid; gap: 18px; color: #1d2b35; }
.event-hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(330px, .72fr); gap: 38px; align-items: end; padding: 30px 34px; color: #f8fbfc; background: linear-gradient(122deg, #102a3a, #173f51); border-radius: 4px; box-shadow: 0 18px 42px rgba(20, 48, 63, .16); }
.back-link { color: #a9d9e7; font-size: 11px; font-weight: 700; text-decoration: none; }
.hero-copy p { margin: 28px 0 7px; color: #83c4d7; font: 750 10px/1.2 var(--mono); letter-spacing: .11em; }
.hero-copy h1 { margin: 0; font-size: clamp(30px, 4vw, 50px); line-height: 1.04; letter-spacing: -.05em; }
.hero-copy strong { display: inline-block; margin-top: 14px; color: #f5b17f; font-size: 12px; }
.chat-entry { display: flex; width: fit-content; align-items: center; gap: 10px; margin-top: 22px; padding: 10px 12px; color: #102a3a; background: #f5b17f; border: 1px solid rgba(255,255,255,.34); border-radius: 3px; font-size: 11px; font-weight: 800; text-decoration: none; transition: transform .18s ease, background .18s ease; }
.chat-entry span { color: #5b321e; font: 800 8px/1 var(--mono); letter-spacing: .08em; }
.chat-entry:hover { transform: translateY(-1px); background: #ffc79d; }
.chat-entry:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
.event-window { display: grid; gap: 1px; margin: 0; background: rgba(255,255,255,.17); }
.event-window div { padding: 13px 15px; background: rgba(8, 29, 40, .64); }
.event-window dt { color: #8fb2c1; font-size: 9px; }
.event-window dd { margin: 5px 0 0; color: #eef6f8; font: 650 10px/1.5 var(--mono); }
.reading-card { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(360px, .75fr); overflow: hidden; background: #fff; border: 1px solid #d8dfe3; border-left: 5px solid var(--orange); }
.reading-card > div { padding: 23px 26px; }
.reading-card span, .section-heading span { color: var(--orange); font: 800 9px/1.2 var(--mono); letter-spacing: .08em; }
.reading-card h2 { margin: 6px 0 8px; color: var(--navy); font-size: 20px; }
.reading-card p { max-width: 820px; margin: 0; color: #52616b; font-size: 12px; line-height: 1.8; }
.reading-card dl { display: grid; grid-template-columns: repeat(3, 1fr); margin: 0; background: #e1e7ea; }
.reading-card dl div { display: grid; align-content: center; gap: 5px; padding: 16px; background: #f8fafb; }
.reading-card dt { color: #66747d; font-size: 9px; }
.reading-card dd { margin: 0; color: var(--navy); font: 800 24px/1 var(--mono); }
.sheet { min-width: 0; overflow: hidden; padding: 22px 24px 24px; background: #fff; border: 1px solid #d8dfe3; border-radius: 4px; box-shadow: 0 5px 18px rgba(24, 47, 61, .05); scroll-margin-top: 78px; }
.section-heading { display: flex; justify-content: space-between; gap: 28px; align-items: end; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid #dfe5e8; }
.section-heading div { display: flex; gap: 13px; align-items: baseline; }
.section-heading span { font-size: 13px; }
.section-heading h2 { margin: 0; color: var(--navy); font-size: 20px; letter-spacing: -.025em; }
.section-heading p { max-width: 560px; margin: 0; color: #63727b; font-size: 10px; line-height: 1.55; text-align: right; }
.inline-facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin-top: 12px; background: #dbe2e6; border: 1px solid #dbe2e6; }
.inline-facts p { display: flex; justify-content: space-between; gap: 10px; margin: 0; padding: 12px 14px; background: #f8fafb; }
.inline-facts span { color: #687781; font-size: 10px; }
.inline-facts b { color: var(--navy); font: 800 15px/1 var(--mono); }
.ip-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.ip-grid figure { min-width: 0; margin: 0; padding: 14px; background: #fbfcfc; border: 1px solid #dfe5e8; }
.ip-grid figcaption { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.ip-grid figcaption b { color: var(--navy); font-size: 13px; }
.ip-grid figcaption span, .ip-grid figure > p { color: #71808a; font-size: 9px; }
.ip-grid figure > p { margin: 8px 4px 0; }
.filter-bar { display: grid; grid-template-columns: minmax(220px, 1fr) 250px 94px; align-items: end; gap: 10px; margin-bottom: 16px; padding: 13px; background: var(--cream); border: 1px solid #e1dbd0; }
.filter-bar.is-path { grid-template-columns: 190px minmax(220px, 1fr) 220px 94px; }
.filter-bar label { display: grid; gap: 6px; }
.filter-bar label span { color: #6e655b; font-size: 9px; font-weight: 700; }
.filter-bar input, .filter-bar select { width: 100%; height: 38px; padding: 0 10px; color: #23333d; background: #fff; border: 1px solid #bfc8cd; border-radius: 3px; }
.filter-bar button, .pager button { min-height: 38px; cursor: pointer; color: #fff; background: var(--navy); border: 1px solid var(--navy); border-radius: 3px; font-weight: 700; }
.table-scroll { min-width: 0; width: 100%; max-width: 100%; overflow-x: auto; border: 1px solid #dbe2e6; }
table { width: 100%; min-width: 1100px; border-collapse: collapse; }
th { padding: 10px 12px; color: #667680; background: #f1f4f5; font-size: 9px; text-align: left; }
td { padding: 12px; color: #42515b; border-top: 1px solid #e3e8eb; font: 650 10px/1.4 var(--mono); vertical-align: middle; }
td:first-child { display: grid; min-width: 230px; gap: 3px; }
td a { color: var(--blue); font-weight: 800; text-decoration: none; }
td b { color: #2d3c46; font: 700 11px/1.3 var(--sans); }
td small { color: #7f8c94; font: 9px/1.3 var(--sans); }
td em { display: inline-block; padding: 4px 7px; color: #8a4a24; background: #fff0e5; border-radius: 10px; font: normal 700 9px/1.2 var(--sans); white-space: nowrap; }
td em.route_interrupted { color: #7c2f2c; background: #f9e4e2; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 15px; }
.pager button { min-width: 110px; padding: 0 13px; }
.pager button:disabled { cursor: not-allowed; opacity: .36; }
.pager span { color: #66747d; font: 650 10px/1.3 var(--mono); }
.relation-list { display: grid; border: 1px solid #dbe2e6; }
.relation-list > article { display: grid; grid-template-columns: 36px minmax(210px, .72fr) minmax(360px, 1fr); gap: 14px; align-items: center; padding: 14px; }
.relation-list > article + article { border-top: 1px solid #dbe2e6; }
.relation-index { color: #98a4aa; font: 750 10px/1 var(--mono); }
.relation-main h3, .relation-main p { margin: 0; }
.relation-main h3 { color: var(--navy); font-size: 13px; }
.relation-main h3 span { margin: 0 5px; color: var(--orange); font-size: 9px; }
.relation-main p { margin-top: 4px; color: #77858d; font-size: 9px; }
.relation-list dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px 0; margin: 0; }
.relation-list dl div { padding: 5px 10px; border-left: 1px solid #e0e6e9; }
.relation-list dt { color: #78868e; font-size: 8px; }
.relation-list dd { margin: 4px 0 0; color: #263944; font: 750 13px/1 var(--mono); }
.relation-list details { grid-column: 2 / -1; }
.relation-list summary { cursor: pointer; color: var(--blue); font-size: 10px; font-weight: 750; }
.relation-list ol { display: grid; gap: 1px; margin: 10px 0 0; padding: 0; background: #dfe5e8; list-style: none; }
.relation-list li { display: grid; grid-template-columns: minmax(150px, .25fr) minmax(300px, 1fr) auto; gap: 12px; padding: 9px 11px; background: #f8fafb; }
.relation-list li b { color: #394b56; font: 700 9px/1.4 var(--mono); }
.relation-list code { overflow-wrap: anywhere; color: #254e62; font: 8px/1.5 var(--mono); }
.relation-list li small { color: #75838c; font-size: 8px; white-space: nowrap; }
@media (max-width: 920px) {
  .event-hero, .reading-card, .ip-grid { grid-template-columns: 1fr; }
  .reading-card dl { min-height: 110px; }
  .filter-bar, .filter-bar.is-path { grid-template-columns: 1fr 1fr; }
  .relation-list > article { grid-template-columns: 28px 1fr; }
  .relation-list dl, .relation-list details { grid-column: 2; }
}
@media (max-width: 620px) {
  .event-hero { padding: 23px 20px; }
  .sheet { padding: 18px 14px; }
  .reading-card dl, .inline-facts { grid-template-columns: 1fr; }
  .section-heading { display: grid; }
  .section-heading p { text-align: left; }
  .filter-bar, .filter-bar.is-path { grid-template-columns: 1fr; }
  .relation-list dl { grid-template-columns: 1fr; }
  .relation-list dl div { border-left: 0; border-top: 1px solid #e0e6e9; }
  .relation-list li { grid-template-columns: 1fr; }
  .pager { flex-wrap: wrap; justify-content: center; }
}
</style>
