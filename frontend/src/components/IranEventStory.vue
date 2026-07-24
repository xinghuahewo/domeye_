<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import LineChart, { type ChartMarker, type ChartSeries } from '@/components/LineChart.vue'
import type {
  EventStory,
  EventStoryAffectedAsn,
  StoryClaimLevel,
} from '@/types/api'

const props = defineProps<{
  story: EventStory
}>()

const stateLabels: Record<EventStoryAffectedAsn['end_classification'], string> = {
  fully_visible: '窗口末可见',
  partially_visible: '窗口末部分可见',
  fully_invisible: '窗口末全不可见',
  unknown: '窗口末未知',
}

const claimLabels: Record<StoryClaimLevel, string> = {
  fact: '观测事实',
  derived: '规则推导',
  inference: '有限推断',
  unknown: '当前未知',
}

const comparisonLabels: Record<string, string> = {
  unverifiable: '原口径不可复现',
  internally_consistent: '旧事实内部一致',
  verified_fixed_cohort: '固定人口已验证',
}

const visibilitySeries = computed<ChartSeries[]>(() => [
  {
    name: '受影响 ASN',
    color: '#d84a3a',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.affected_asn_ratio * 100,
    ]),
  },
  {
    name: 'Prefix×VP 可见率',
    color: '#0b57b7',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.visible_prefix_vp_ratio * 100,
    ]),
  },
  {
    name: 'IPv4 Prefix×VP',
    color: '#1c8f6a',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.ipv4_visible_prefix_vp_ratio * 100,
    ]),
  },
  {
    name: 'IPv6 Prefix×VP',
    color: '#7656b5',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.ipv6_visible_prefix_vp_ratio * 100,
    ]),
  },
])

const updateSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.announce_count,
    ]),
  },
  {
    name: 'WITHDRAW',
    color: '#e07025',
    data: props.story.series.map((point) => [
      point.observed_at_utc,
      point.withdraw_count,
    ]),
  },
])

const markers = computed<ChartMarker[]>(() => [
  {
    time: props.story.detection.onset.at_utc,
    label: '窗口起点已异常',
    color: '#b54708',
  },
  {
    time: props.story.detection.detected.at_utc,
    label: '连续两点确认',
    color: '#d84a3a',
  },
  {
    time: props.story.impact.peak.observed_at_utc,
    label: 'ASN 峰值',
    color: '#8d2c25',
  },
  {
    time: props.story.impact.trough.observed_at_utc,
    label: '可见率谷值',
    color: '#7656b5',
  },
])

const visibleDrop = computed(
  () => (1 - props.story.impact.trough.visible_prefix_vp_ratio) * 100,
)

const topPersistentAsns = computed(
  () => props.story.impact.persistent_asns.slice(0, 12),
)

function percent(value: number | null | undefined, digits = 2) {
  return typeof value === 'number'
    ? `${(value * 100).toFixed(digits)}%`
    : '未知'
}

function integer(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '未知'
}

function compactTime(value: string | null | undefined) {
  if (!value) return '未知'
  const normalized = value.replace('T', ' ')
  return normalized.slice(5, 16)
}

function addressFamilies(values: number[]) {
  return values.map((value) => value === 4 ? 'IPv4' : value === 6 ? 'IPv6' : `AFI ${value}`).join(' / ')
}

function durationSlots(count: number) {
  const minutes = count * 5
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return hours > 0 ? `${hours}小时${rest ? `${rest}分` : ''}` : `${minutes}分钟`
}
</script>

<template>
  <article class="story">
    <header class="story-hero">
      <div class="story-hero-main">
        <RouterLink class="story-back" to="/events">← 返回异常事件</RouterLink>
        <p class="story-kicker">INCIDENT BRIEF · RRC25 CONTROL PLANE</p>
        <div class="story-title-row">
          <div>
            <span class="story-country-code">{{ story.event.country_code }}</span>
            <h1>{{ story.event.country_name }} · {{ story.event.label }}</h1>
          </div>
          <span class="story-status">{{ story.event.status_label }}</span>
        </div>
        <p class="story-headline">{{ story.event.headline }}</p>
        <div class="story-boundaries">
          <p><b>观测范围</b>{{ story.event.scope_statement }}</p>
          <p><b>服务影响</b>{{ story.event.service_impact_statement }}</p>
        </div>
      </div>

      <aside class="story-identity" aria-label="事件身份与数据状态">
        <div class="identity-status">
          <span>当前判断</span>
          <strong>ONGOING</strong>
          <small>进行中 · 未确认恢复</small>
        </div>
        <dl>
          <div><dt>INCIDENT</dt><dd>{{ story.event.incident_id }}</dd></div>
          <div><dt>COLLECTOR</dt><dd>{{ story.observation.collector_id.toUpperCase() }}</dd></div>
          <div><dt>QUALITY</dt><dd>{{ story.observation.data_freshness.quality_status.toUpperCase() }}</dd></div>
          <div><dt>OBSERVATIONS</dt><dd>{{ story.observation.observation_count }} / {{ story.observation.observation_count }}</dd></div>
          <div><dt>LAST OBSERVED</dt><dd>{{ compactTime(story.observation.window_end_local) }}</dd></div>
        </dl>
      </aside>
    </header>

    <section class="metric-rack" aria-label="事件核心指标">
      <article>
        <span>峰值受影响 ASN</span>
        <strong>{{ story.impact.peak.affected_asn_count }}<small>/ {{ story.observation.cohort.baseline_origin_asn_count }}</small></strong>
        <p>{{ percent(story.impact.peak.affected_asn_ratio) }} · {{ compactTime(story.impact.peak.observed_at_local) }}</p>
      </article>
      <article>
        <span>全不可见 / 部分可见</span>
        <strong>{{ story.impact.peak.fully_invisible_asn_count }}<small>/ {{ story.impact.peak.partially_visible_asn_count }}</small></strong>
        <p>同快照 · 双栈联合分类</p>
      </article>
      <article class="is-alert">
        <span>Prefix×VP 最大不可见</span>
        <strong>{{ visibleDrop.toFixed(2) }}<small>%</small></strong>
        <p>谷值可见率 {{ percent(story.impact.trough.visible_prefix_vp_ratio) }}</p>
      </article>
      <article>
        <span>窗口末仍受影响</span>
        <strong>{{ story.impact.window_end.affected_asn_count }}<small>/ {{ story.observation.cohort.baseline_origin_asn_count }}</small></strong>
        <p>{{ compactTime(story.impact.window_end.observed_at_local) }} · 尚未恢复</p>
      </article>
    </section>

    <section class="context-grid">
      <article class="context-card is-scope">
        <header>
          <span>01 / OBSERVATION</span>
          <h2>我们观察了什么</h2>
        </header>
        <dl class="scope-facts">
          <div><dt>观察窗口</dt><dd>{{ compactTime(story.observation.window_start_local) }} — {{ compactTime(story.observation.window_end_local) }}</dd></div>
          <div><dt>观测点</dt><dd>{{ story.observation.vantage_point_count }} 个窗口起点唯一 VP</dd></div>
          <div><dt>固定 ASN 人口</dt><dd>{{ integer(story.observation.cohort.baseline_origin_asn_count) }}</dd></div>
          <div><dt>固定 Prefix×VP</dt><dd>{{ integer(story.observation.cohort.baseline_prefix_vp_count) }}</dd></div>
          <div><dt>状态粒度</dt><dd>{{ story.observation.interval_seconds / 60 }} 分钟</dd></div>
          <div><dt>窗口完整度</dt><dd>{{ story.observation.observation_count }}/{{ story.observation.observation_count }} 状态点</dd></div>
        </dl>
        <p class="context-note">{{ story.observation.coverage_statement }}</p>
      </article>

      <article class="context-card is-baseline">
        <header>
          <span>02 / NORMAL BASELINE</span>
          <h2>{{ story.baseline.label }}</h2>
        </header>
        <div class="unknown-seal">UNKNOWN</div>
        <p>{{ story.baseline.reason }}</p>
        <p class="context-note">{{ story.baseline.consequence }}</p>
      </article>
    </section>

    <section class="story-section trend-section">
      <div class="section-title">
        <div>
          <span>03 / CHANGE & DETECTION</span>
          <h2>可见性下降如何演化</h2>
        </div>
        <p>所有比例均使用同一固定 cohort；图中不含用户流量或服务可用性。</p>
      </div>
      <LineChart
        :series="visibilitySeries"
        :markers="markers"
        unit="%"
        :height="340"
        timezone="Asia/Shanghai"
      />
      <div class="detection-rule">
        <span>检测规则</span>
        <strong>{{ story.detection.rule.statement }}</strong>
        <p>{{ story.detection.onset.statement }}</p>
      </div>
    </section>

    <section class="story-section milestone-section">
      <div class="section-title">
        <div>
          <span>04 / TIME SEMANTICS</span>
          <h2>五种时间，不再混为“事件时间”</h2>
        </div>
        <p>旧记录时间保留为前候选身份，不替代状态重放的 onset 或 detected。</p>
      </div>
      <ol class="milestone-track">
        <li class="is-legacy">
          <span>旧事实记录</span>
          <strong>{{ compactTime(story.detection.legacy_record.at_local) }}</strong>
          <p>{{ story.detection.legacy_record.semantics }}</p>
        </li>
        <li class="is-censored">
          <span>状态起点</span>
          <strong>{{ compactTime(story.detection.onset.at_local) }}</strong>
          <p>左删失 · 精确开始时间未知</p>
        </li>
        <li>
          <span>连续确认</span>
          <strong>{{ compactTime(story.detection.detected.at_local) }}</strong>
          <p>达到检测门槛</p>
        </li>
        <li class="is-peak">
          <span>ASN 影响峰值</span>
          <strong>{{ compactTime(story.impact.peak.observed_at_local) }}</strong>
          <p>{{ story.impact.peak.affected_asn_count }} 个 ASN 受影响</p>
        </li>
        <li class="is-trough">
          <span>可见率谷值</span>
          <strong>{{ compactTime(story.impact.trough.observed_at_local) }}</strong>
          <p>{{ percent(story.impact.trough.visible_prefix_vp_ratio) }}</p>
        </li>
        <li class="is-open">
          <span>观测结束</span>
          <strong>{{ compactTime(story.observation.window_end_local) }}</strong>
          <p>右删失 · 事件仍进行中</p>
        </li>
      </ol>
    </section>

    <section class="impact-layout">
      <article class="story-section family-panel">
        <div class="section-title">
          <div>
            <span>05 / ADDRESS FAMILY</span>
            <h2>IPv4 与 IPv6 表现不同</h2>
          </div>
        </div>
        <div class="family-grid">
          <article>
            <span>IPv4 · 谷值</span>
            <strong>{{ percent(story.impact.trough.ipv4_visible_prefix_vp_ratio) }}</strong>
            <p>{{ integer(story.impact.trough.ipv4_visible_prefix_vp_count) }} / {{ integer(story.impact.trough.ipv4_baseline_prefix_vp_count) }} Prefix×VP</p>
          </article>
          <article>
            <span>IPv6 · 谷值</span>
            <strong>{{ percent(story.impact.trough.ipv6_visible_prefix_vp_ratio) }}</strong>
            <p>{{ integer(story.impact.trough.ipv6_visible_prefix_vp_count) }} / {{ integer(story.impact.trough.ipv6_baseline_prefix_vp_count) }} Prefix×VP</p>
          </article>
        </div>
        <div class="impact-statements">
          <p>{{ story.impact.peak_statement }}</p>
          <p>{{ story.impact.trough_statement }}</p>
          <p>{{ story.impact.end_statement }}</p>
        </div>
      </article>

      <article class="story-section update-panel">
        <div class="section-title">
          <div>
            <span>06 / BGP UPDATES</span>
            <h2>报文活动是旁证，不是影响人口</h2>
          </div>
        </div>
        <LineChart
          :series="updateSeries"
          :markers="markers"
          unit=" 条"
          :height="280"
          timezone="Asia/Shanghai"
        />
      </article>
    </section>

    <section class="story-section asn-section">
      <div class="section-title">
        <div>
          <span>07 / CONCENTRATION</span>
          <h2>持续受影响的 ASN</h2>
        </div>
        <p>{{ story.impact.ranking_semantics }}</p>
      </div>
      <div class="asn-table-wrap">
        <table class="asn-table">
          <thead>
            <tr>
              <th>ASN</th>
              <th>地址族</th>
              <th>固定前缀</th>
              <th>Prefix×VP 人口</th>
              <th>全不可见时长</th>
              <th>窗口末状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="asn in topPersistentAsns" :key="asn.asn">
              <td><strong>AS{{ asn.asn }}</strong></td>
              <td>{{ addressFamilies(asn.address_families) }}</td>
              <td>{{ integer(asn.baseline_prefix_count) }}</td>
              <td>{{ integer(asn.baseline_prefix_vp_count) }}</td>
              <td>{{ durationSlots(asn.fully_invisible_slot_count) }}</td>
              <td><span :class="['asn-state', `is-${asn.end_classification}`]">{{ stateLabels[asn.end_classification] }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="lifecycle-layout">
      <article class="story-section lifecycle-panel">
        <div class="section-title">
          <div>
            <span>08 / EVOLUTION</span>
            <h2>一次 episode、一个 wave，仍在进行</h2>
          </div>
        </div>
        <div class="lifecycle-state">
          <span>ONGOING</span>
          <strong>出现回升，但没有恢复确认</strong>
          <p>{{ story.lifecycle.rebound_statement }}</p>
          <small>{{ story.lifecycle.recovery_rule }}</small>
        </div>
        <dl class="lifecycle-facts">
          <div><dt>Episode</dt><dd>{{ story.lifecycle.episode_count }}</dd></div>
          <div><dt>Wave</dt><dd>{{ story.lifecycle.wave_count }}</dd></div>
          <div><dt>部分恢复</dt><dd>{{ story.lifecycle.partial_recovery_at_local ? compactTime(story.lifecycle.partial_recovery_at_local) : '未确认' }}</dd></div>
          <div><dt>完全恢复</dt><dd>{{ story.lifecycle.full_recovery_at_local ? compactTime(story.lifecycle.full_recovery_at_local) : '未确认' }}</dd></div>
        </dl>
      </article>

      <article class="story-section precursor-panel">
        <div class="section-title">
          <div>
            <span>09 / PRECURSOR</span>
            <h2>前候选事件：只有时间关系</h2>
          </div>
        </div>
        <p>{{ story.precursor.statement }}</p>
        <dl>
          <div><dt>时间关系</dt><dd>较早出现</dd></div>
          <div><dt>因果关系</dt><dd>未评估</dd></div>
          <div><dt>结论等级</dt><dd>未知</dd></div>
        </dl>
      </article>
    </section>

    <section class="story-section claim-section">
      <div class="section-title">
        <div>
          <span>10 / CLAIM → EVIDENCE</span>
          <h2>结论旁边就是证据和边界</h2>
        </div>
        <p>事实、规则推导和未知分别表达；证据 ID 不替代结论解释。</p>
      </div>
      <div class="claim-grid">
        <article v-for="claim in story.claims" :key="claim.claim_id" :class="['claim-card', `is-${claim.level}`]">
          <header>
            <span>{{ claimLabels[claim.level] }}</span>
            <small>{{ claim.confidence }}</small>
          </header>
          <h3>{{ claim.title }}</h3>
          <p>{{ claim.statement }}</p>
          <dl>
            <div><dt>适用范围</dt><dd>{{ claim.scope }}</dd></div>
            <div><dt>证据</dt><dd><code v-for="item in claim.evidence_refs" :key="item">{{ item }}</code></dd></div>
          </dl>
        </article>
      </div>
    </section>

    <section class="story-section comparison-section">
      <div class="section-title">
        <div>
          <span>11 / RECONCILIATION</span>
          <h2>三个数字组不能直接相减</h2>
        </div>
        <p>人口、时点和分类定义不同；新重放不以复现旧数字为目标。</p>
      </div>
      <div class="comparison-grid">
        <article v-for="item in story.comparisons" :key="item.source">
          <span>{{ comparisonLabels[item.status] || item.status }}</span>
          <h3>{{ item.source }}</h3>
          <strong>{{ item.value }}</strong>
          <p>{{ item.explanation }}</p>
        </article>
      </div>
    </section>

    <section class="unknown-action-layout">
      <article class="story-section unknown-panel">
        <div class="section-title">
          <div>
            <span>12 / UNKNOWNS</span>
            <h2>现在仍不能回答什么</h2>
          </div>
        </div>
        <ol class="unknown-list">
          <li v-for="(item, index) in story.unknowns" :key="item.question">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <div>
              <h3>{{ item.question }}</h3>
              <p><b>原因</b>{{ item.reason }}</p>
              <p><b>所需证据</b>{{ item.evidence_needed }}</p>
              <p><b>下一步</b>{{ item.next_action }}</p>
            </div>
          </li>
        </ol>
      </article>

      <aside class="action-panel">
        <span class="action-kicker">NEXT ACTIONS</span>
        <h2>下一步研判顺序</h2>
        <ol>
          <li v-for="action in story.actions" :key="action.priority">
            <b>{{ String(action.priority).padStart(2, '0') }}</b>
            <div><strong>{{ action.label }}</strong><p>{{ action.reason }}</p></div>
          </li>
        </ol>
      </aside>
    </section>

    <details class="audit-panel">
      <summary>
        <span>审计与交付身份</span>
        <b>{{ story.evidence.engine_version }}</b>
      </summary>
      <div class="audit-grid">
        <dl>
          <div><dt>RIB</dt><dd>{{ story.evidence.input_summary.rib_count }}</dd></div>
          <div><dt>Catch-up UPDATE</dt><dd>{{ story.evidence.input_summary.catch_up_update_count }}</dd></div>
          <div><dt>正式 UPDATE</dt><dd>{{ story.evidence.input_summary.formal_update_count }}</dd></div>
          <div><dt>RouteEvent</dt><dd>{{ integer(story.evidence.input_summary.update_route_events) }}</dd></div>
          <div><dt>路由状态行</dt><dd>{{ integer(story.evidence.route_state_file.row_count) }}</dd></div>
          <div><dt>请求时校验</dt><dd>{{ story.evidence.consumed_deliverable_hashes_verified ? '已校验所消费文件' : '未校验' }}</dd></div>
        </dl>
        <div>
          <p>{{ story.evidence.route_state_file.statement }}</p>
          <code v-for="(hash, filename) in story.evidence.verified_hashes" :key="filename">{{ filename }} · {{ hash }}</code>
        </div>
      </div>
    </details>
  </article>
</template>

<style scoped>
.story {
  display: grid;
  gap: 16px;
  color: #1d2935;
}

.story-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(310px, .55fr);
  overflow: hidden;
  color: #f5f7f8;
  background: #15232d;
  border: 1px solid #263945;
  border-radius: var(--radius);
  box-shadow: 0 18px 48px rgba(17, 31, 43, .16);
}

.story-hero::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 5px;
  content: "";
  background: #e06436;
}

.story-hero-main {
  min-width: 0;
  padding: 26px 30px 28px 34px;
}

.story-back {
  display: inline-block;
  margin-bottom: 23px;
  color: #9fc9f7;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
}

.story-kicker,
.section-title span,
.context-card header span,
.action-kicker {
  color: #478fd6;
  font: 760 9px/1.2 var(--mono);
  letter-spacing: .1em;
}

.story-title-row {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 18px;
}

.story-country-code {
  color: #92a6b4;
  font: 800 10px/1 var(--mono);
  letter-spacing: .12em;
}

.story h1 {
  margin: 6px 0 0;
  color: #f8fafb;
  font-size: clamp(28px, 3.5vw, 46px);
  line-height: 1.05;
  letter-spacing: -.045em;
}

.story-status {
  flex: 0 0 auto;
  padding: 7px 10px;
  color: #ffd8c4;
  background: rgba(224, 100, 54, .12);
  border: 1px solid #8d4d35;
  border-radius: 3px;
  font: 750 9px/1.2 var(--mono);
}

.story-headline {
  max-width: 950px;
  margin: 24px 0 22px;
  color: #dce5ea;
  font-size: 16px;
  font-weight: 550;
  line-height: 1.8;
}

.story-boundaries {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.story-boundaries p {
  margin: 0;
  padding: 11px 12px;
  color: #acbdc8;
  background: rgba(255, 255, 255, .035);
  border: 1px solid #334651;
  font-size: 10px;
  line-height: 1.65;
}

.story-boundaries b {
  display: block;
  margin-bottom: 3px;
  color: #6fa9dd;
  font: 720 8px/1.2 var(--mono);
  letter-spacing: .07em;
}

.story-identity {
  display: grid;
  grid-template-rows: auto 1fr;
  min-width: 0;
  background: #1a2b36;
  border-left: 1px solid #30434f;
}

.identity-status {
  display: grid;
  gap: 5px;
  padding: 26px 24px 21px;
  background: #20333e;
  border-bottom: 1px solid #30434f;
}

.identity-status span,
.story-identity dt {
  color: #7f98a7;
  font: 720 8px/1.2 var(--mono);
  letter-spacing: .09em;
}

.identity-status strong {
  color: #f0a078;
  font: 820 30px/1 var(--mono);
  letter-spacing: -.04em;
}

.identity-status small {
  color: #bdc9d0;
  font-size: 10px;
}

.story-identity dl {
  display: grid;
  align-content: start;
  margin: 0;
  padding: 10px 24px 20px;
}

.story-identity dl div {
  min-width: 0;
  padding: 11px 0;
  border-bottom: 1px solid #30434f;
}

.story-identity dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  color: #e1e8ec;
  font: 650 10px/1.5 var(--mono);
}

.metric-rack {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  background: #cad4dc;
  border: 1px solid #cad4dc;
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  gap: 1px;
}

.metric-rack article {
  min-width: 0;
  padding: 18px 20px;
  background: #fff;
}

.metric-rack article.is-alert {
  box-shadow: inset 0 3px #d84a3a;
}

.metric-rack span {
  color: #6e7b87;
  font-size: 10px;
  font-weight: 650;
}

.metric-rack strong {
  display: block;
  margin: 10px 0 7px;
  color: #18242e;
  font: 800 31px/1 var(--mono);
  letter-spacing: -.045em;
}

.metric-rack small {
  margin-left: 5px;
  color: #7b8994;
  font-size: 13px;
}

.metric-rack p {
  margin: 0;
  color: #74818c;
  font-size: 9px;
  line-height: 1.5;
}

.context-grid,
.impact-layout,
.lifecycle-layout,
.unknown-action-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr);
  gap: 16px;
}

.context-card,
.story-section {
  min-width: 0;
  padding: 21px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.context-card header {
  margin-bottom: 16px;
}

.context-card h2,
.section-title h2,
.action-panel h2 {
  margin: 5px 0 0;
  color: #17232d;
  font-size: 19px;
  letter-spacing: -.025em;
}

.scope-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: #e2e8ed;
  border: 1px solid #e2e8ed;
}

.scope-facts div {
  min-width: 0;
  padding: 12px;
  background: #f8fafb;
}

.scope-facts dt,
.lifecycle-facts dt,
.precursor-panel dt,
.claim-card dt,
.audit-panel dt {
  color: #87939d;
  font-size: 8px;
}

.scope-facts dd,
.lifecycle-facts dd,
.precursor-panel dd,
.audit-panel dd {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
  color: #34424d;
  font: 650 9px/1.45 var(--mono);
}

.context-note {
  margin: 14px 0 0;
  padding: 10px 12px;
  color: #5d6d78;
  background: #f5f7f9;
  border-left: 3px solid #7f95a5;
  font-size: 10px;
  line-height: 1.65;
}

.is-baseline {
  position: relative;
  overflow: hidden;
  border-color: #e3d7c4;
  background: #fffdf9;
}

.is-baseline > p:not(.context-note) {
  max-width: 600px;
  margin: 23px 0 0;
  color: #5d5143;
  font-size: 11px;
  line-height: 1.75;
}

.unknown-seal {
  position: absolute;
  top: 22px;
  right: 20px;
  padding: 5px 7px;
  color: #a25c1e;
  border: 1px solid #d7ab7f;
  transform: rotate(-2deg);
  font: 800 9px/1 var(--mono);
  letter-spacing: .08em;
}

.section-title {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 16px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--line);
}

.section-title > p {
  max-width: 540px;
  margin: 0;
  color: #77848f;
  font-size: 9px;
  line-height: 1.55;
  text-align: right;
}

.detection-rule {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(260px, .8fr);
  gap: 14px;
  align-items: center;
  margin-top: 12px;
  padding: 12px 14px;
  color: #60451f;
  background: #fff8eb;
  border: 1px solid #ead7b4;
  border-left: 4px solid #e07025;
}

.detection-rule span {
  color: #9c5c18;
  font: 750 8px/1.2 var(--mono);
  letter-spacing: .06em;
}

.detection-rule strong {
  color: #4f3a20;
  font-size: 11px;
}

.detection-rule p {
  margin: 0;
  color: #806847;
  font-size: 9px;
  line-height: 1.55;
}

.milestone-track {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line);
}

.milestone-track li {
  position: relative;
  min-width: 0;
  min-height: 122px;
  padding: 14px 12px;
  background: #fbfcfd;
  border-right: 1px solid var(--line);
  box-shadow: inset 0 3px #4d8dc4;
}

.milestone-track li:last-child { border-right: 0; }
.milestone-track li.is-legacy { box-shadow: inset 0 3px #8e9aa4; }
.milestone-track li.is-censored { box-shadow: inset 0 3px #e07025; }
.milestone-track li.is-peak { box-shadow: inset 0 3px #d84a3a; }
.milestone-track li.is-trough { box-shadow: inset 0 3px #7656b5; }
.milestone-track li.is-open { box-shadow: inset 0 3px #e07025; }

.milestone-track span {
  color: #7d8994;
  font-size: 9px;
}

.milestone-track strong {
  display: block;
  margin: 13px 0 8px;
  color: #25333e;
  font: 730 14px/1.25 var(--mono);
}

.milestone-track p {
  margin: 0;
  color: #72808b;
  font-size: 9px;
  line-height: 1.55;
}

.impact-layout {
  grid-template-columns: minmax(0, .85fr) minmax(0, 1.15fr);
}

.family-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.family-grid article {
  padding: 15px;
  background: #f7f9fb;
  border: 1px solid #e1e7ec;
  border-top: 3px solid #1c8f6a;
}

.family-grid article + article {
  border-top-color: #7656b5;
}

.family-grid span {
  color: #74818b;
  font-size: 9px;
}

.family-grid strong {
  display: block;
  margin: 8px 0;
  color: #25323c;
  font: 780 24px/1 var(--mono);
}

.family-grid p,
.impact-statements p {
  margin: 0;
  color: #65727c;
  font-size: 9px;
  line-height: 1.55;
}

.impact-statements {
  display: grid;
  gap: 1px;
  margin-top: 12px;
  background: #e2e8ed;
}

.impact-statements p {
  padding: 10px 11px;
  background: #fbfcfd;
}

.asn-table-wrap {
  overflow-x: auto;
}

.asn-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 10px;
}

.asn-table th {
  padding: 9px 11px;
  color: #77838e;
  background: #f5f7f9;
  border-bottom: 1px solid #d8e0e6;
  font-size: 8px;
  font-weight: 700;
  text-align: left;
  white-space: nowrap;
}

.asn-table td {
  padding: 11px;
  color: #53616d;
  border-bottom: 1px solid #e5eaee;
  font-family: var(--mono);
  white-space: nowrap;
}

.asn-table td strong {
  color: #25323d;
}

.asn-state {
  display: inline-block;
  padding: 4px 6px;
  border-radius: 3px;
  color: #4f5e69;
  background: #edf1f4;
  font: 700 8px/1.2 var(--mono);
}

.asn-state.is-fully_invisible {
  color: #a5342b;
  background: #fdeceb;
}

.asn-state.is-partially_visible {
  color: #98601e;
  background: #fff4e2;
}

.asn-state.is-fully_visible {
  color: #176b50;
  background: #e9f6f0;
}

.lifecycle-layout {
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr);
}

.lifecycle-state {
  padding: 17px;
  color: #d9e2e7;
  background: #1b2a34;
  border-left: 4px solid #e07025;
}

.lifecycle-state > span {
  color: #ee9c6e;
  font: 800 9px/1.2 var(--mono);
  letter-spacing: .09em;
}

.lifecycle-state strong {
  display: block;
  margin: 8px 0;
  color: #f2f5f6;
  font-size: 15px;
}

.lifecycle-state p {
  margin: 0;
  color: #b9c7cf;
  font-size: 10px;
  line-height: 1.65;
}

.lifecycle-state small {
  display: block;
  margin-top: 11px;
  color: #8197a5;
  font-size: 8px;
  line-height: 1.55;
}

.lifecycle-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 12px 0 0;
  background: var(--line);
  border: 1px solid var(--line);
}

.lifecycle-facts div {
  padding: 11px;
  background: #f8fafb;
}

.precursor-panel > p {
  margin: 0 0 16px;
  color: #586873;
  font-size: 11px;
  line-height: 1.75;
}

.precursor-panel > dl {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1px;
  margin: 0;
  background: var(--line);
}

.precursor-panel > dl div {
  padding: 11px;
  background: #f8fafb;
}

.claim-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.claim-card {
  padding: 16px;
  background: #fbfcfd;
  border: 1px solid #dce3e8;
  border-top: 3px solid #1c8f6a;
  border-radius: 4px;
}

.claim-card.is-derived { border-top-color: #478fd6; }
.claim-card.is-inference { border-top-color: #e07025; }
.claim-card.is-unknown { border-top-color: #929da6; }

.claim-card header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.claim-card header span {
  color: #4e6576;
  font: 750 8px/1.2 var(--mono);
  letter-spacing: .06em;
}

.claim-card header small {
  color: #8a969f;
  font: 7px/1.2 var(--mono);
}

.claim-card h3 {
  margin: 11px 0 7px;
  color: #24323d;
  font-size: 14px;
}

.claim-card > p {
  margin: 0;
  color: #596873;
  font-size: 10px;
  line-height: 1.65;
}

.claim-card dl {
  display: grid;
  gap: 8px;
  margin: 13px 0 0;
  padding-top: 12px;
  border-top: 1px solid #e1e7eb;
}

.claim-card dl div {
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  gap: 8px;
}

.claim-card dd {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
  color: #586671;
  font-size: 9px;
  line-height: 1.5;
}

.claim-card code {
  padding: 3px 5px;
  overflow-wrap: anywhere;
  color: #345d7d;
  background: #edf3f7;
  font: 7px/1.4 var(--mono);
}

.comparison-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  background: #dce3e8;
  border: 1px solid #dce3e8;
}

.comparison-grid article {
  min-width: 0;
  padding: 16px;
  background: #fbfcfd;
}

.comparison-grid span {
  color: #8a6338;
  font: 700 8px/1.2 var(--mono);
}

.comparison-grid h3 {
  margin: 8px 0 6px;
  color: #33414c;
  font-size: 12px;
}

.comparison-grid strong {
  display: block;
  overflow-wrap: anywhere;
  color: #172630;
  font: 740 14px/1.45 var(--mono);
}

.comparison-grid p {
  margin: 9px 0 0;
  color: #6c7882;
  font-size: 9px;
  line-height: 1.6;
}

.unknown-action-layout {
  align-items: start;
}

.unknown-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.unknown-list li {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid var(--line);
}

.unknown-list li:last-child { border-bottom: 0; }

.unknown-list > li > span {
  color: #a0aab2;
  font: 800 15px/1 var(--mono);
}

.unknown-list h3 {
  margin: 0 0 8px;
  color: #2b3944;
  font-size: 12px;
}

.unknown-list p {
  margin: 4px 0 0;
  color: #65727c;
  font-size: 9px;
  line-height: 1.55;
}

.unknown-list p b {
  display: inline-block;
  width: 56px;
  color: #89949d;
  font-size: 8px;
}

.action-panel {
  position: sticky;
  top: 82px;
  padding: 22px;
  color: #eef3f5;
  background: #1c2d37;
  border: 1px solid #2e414d;
  border-radius: var(--radius);
  box-shadow: 0 14px 32px rgba(17, 31, 43, .12);
}

.action-panel h2 {
  color: #f0f4f6;
}

.action-panel ol {
  display: grid;
  gap: 1px;
  margin: 19px 0 0;
  padding: 0;
  list-style: none;
  background: #334650;
}

.action-panel li {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 11px;
  padding: 13px;
  background: #223641;
}

.action-panel li > b {
  color: #718d9c;
  font: 750 11px/1.3 var(--mono);
}

.action-panel li strong {
  color: #e8eef1;
  font-size: 11px;
}

.action-panel li p {
  margin: 4px 0 0;
  color: #9eb0ba;
  font-size: 9px;
  line-height: 1.55;
}

.audit-panel {
  padding: 13px 16px;
  background: #f5f7f9;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

.audit-panel summary {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  cursor: pointer;
  color: #53616c;
  font-size: 10px;
  font-weight: 700;
}

.audit-panel summary b {
  color: #6e7b85;
  font: 650 8px/1.4 var(--mono);
}

.audit-grid {
  display: grid;
  grid-template-columns: minmax(280px, .6fr) minmax(0, 1.4fr);
  gap: 20px;
  margin-top: 15px;
  padding-top: 15px;
  border-top: 1px solid var(--line);
}

.audit-grid dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: var(--line);
}

.audit-grid dl div {
  padding: 10px;
  background: #fff;
}

.audit-grid p {
  margin: 0 0 10px;
  color: #67747e;
  font-size: 9px;
  line-height: 1.6;
}

.audit-grid code {
  display: block;
  padding: 5px 0;
  overflow-wrap: anywhere;
  color: #5b6d79;
  border-bottom: 1px solid #e1e6ea;
  font: 7px/1.45 var(--mono);
}

@media (max-width: 1180px) {
  .story-hero,
  .context-grid,
  .impact-layout,
  .lifecycle-layout,
  .unknown-action-layout {
    grid-template-columns: 1fr;
  }

  .story-identity {
    grid-template-columns: minmax(220px, .55fr) minmax(0, 1.45fr);
    grid-template-rows: auto;
    border-top: 1px solid #30434f;
    border-left: 0;
  }

  .story-identity dl {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .action-panel {
    position: static;
  }
}

@media (max-width: 900px) {
  .metric-rack {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .scope-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .milestone-track {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .milestone-track li:nth-child(3) {
    border-right: 0;
  }

  .milestone-track li:nth-child(-n + 3) {
    border-bottom: 1px solid var(--line);
  }

  .claim-grid,
  .comparison-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .story-hero-main {
    padding: 22px 20px 24px 24px;
  }

  .story-title-row,
  .section-title {
    align-items: start;
    flex-direction: column;
  }

  .story-boundaries,
  .story-identity,
  .story-identity dl,
  .family-grid,
  .lifecycle-facts,
  .precursor-panel > dl,
  .audit-grid {
    grid-template-columns: 1fr;
  }

  .metric-rack,
  .scope-facts,
  .milestone-track {
    grid-template-columns: 1fr;
  }

  .metric-rack article,
  .milestone-track li {
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .section-title > p {
    text-align: left;
  }

  .detection-rule {
    grid-template-columns: 1fr;
  }

  .context-card,
  .story-section {
    padding: 16px;
  }

  .audit-panel summary {
    flex-direction: column;
  }
}
</style>
