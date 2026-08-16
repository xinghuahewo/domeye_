<script setup lang="ts">
import EventTable from '@/components/EventTable.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import type { EventRow } from '@/types/api'

const events: EventRow[] = [
  {
    key: 'preview-high',
    type: '前缀劫持',
    level: 'high',
    startTime: '2026-02-01 00:00:00',
    endTime: '2026-02-01 00:12:00',
    attackerAs: 'AS64513',
    attackedAs: 'AS64512',
    attackerOrg: '异常来源样本',
    attackedOrg: '受影响网络样本',
    attackerCountry: '美国',
    attackedCountry: '中国',
    affectedPrefix: '203.0.113.0/24',
    summary: '检测到源 AS 发生变化，用于验证高风险长文本呈现。',
    detailUrl: 'hijack/2026-02-01 00:00:00/203.0.113.0-24/1/r',
  },
  {
    key: 'preview-middle',
    type: '路由泄漏',
    level: 'middle',
    startTime: '2026-02-01 00:03:00',
    endTime: null,
    attackerAs: 'AS64515',
    attackedAs: 'AS64512',
    attackerOrg: '泄漏来源样本',
    attackedOrg: '受影响网络样本',
    attackerCountry: '法国',
    attackedCountry: '中国',
    affectedPrefix: '198.51.100.0/24',
    summary: 'AS_PATH 传播关系异常。',
    detailUrl: 'leak/2026-02-01 00:03:00/198.51.100.0-24/2/r',
  },
  {
    key: 'preview-low',
    type: 'AS中断',
    level: 'low',
    startTime: null,
    endTime: null,
    attackerAs: '',
    attackedAs: 'AS64517',
    attackerOrg: '',
    attackedOrg: '中断网络样本',
    attackerCountry: '',
    attackedCountry: '新加坡',
    affectedPrefix: '',
    summary: '',
    detailUrl: '',
  },
]

const chartSeries: ChartSeries[] = [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: [
      ['2026-02-01 00:00:00', 120],
      ['2026-02-01 00:03:00', 168],
      ['2026-02-01 00:06:00', null],
      ['2026-02-01 00:09:00', 145],
    ],
  },
  {
    name: 'WITHDRAW',
    color: '#f48120',
    data: [
      ['2026-02-01 00:00:00', 18],
      ['2026-02-01 00:03:00', 46],
      ['2026-02-01 00:06:00', 22],
      ['2026-02-01 00:09:00', 15],
    ],
  },
]
</script>

<template>
  <article class="page specimen-page">
    <header class="specimen-heading">
      <div>
        <p class="eyebrow">开发标本 / Component specimen</p>
        <h1>组件独立预览</h1>
      </div>
      <dl>
        <div><dt>DATA</dt><dd>FIXED</dd></div>
        <div><dt>API</dt><dd>NONE</dd></div>
        <div><dt>ROUTE</dt><dd>/__components</dd></div>
      </dl>
    </header>

    <section class="specimen-block">
      <div class="specimen-index">
        <span>01</span>
        <div>
          <h2>PageState</h2>
          <p>加载、空数据和错误状态并排检查。</p>
        </div>
      </div>
      <div class="state-grid">
        <PageState kind="loading" title="正在同步固定快照" detail="用于检查长时间请求的等待状态" />
        <PageState title="当前范围没有核心异常事件" detail="空结果不是系统故障" />
        <PageState kind="error" title="事件查询失败" detail="开发快照模拟服务异常" />
      </div>
    </section>

    <section class="specimen-block">
      <div class="specimen-index">
        <span>02</span>
        <div>
          <h2>LineChart</h2>
          <p>双序列、缺失点和紧凑视口。</p>
        </div>
      </div>
      <div class="chart-frame">
        <LineChart :series="chartSeries" unit="条" :height="320" />
      </div>
    </section>

    <section class="specimen-block">
      <div class="specimen-index">
        <span>03</span>
        <div>
          <h2>EventTable</h2>
          <p>风险等级、缺失字段、长摘要与禁用操作。</p>
        </div>
      </div>
      <EventTable :events="events" />
    </section>
  </article>
</template>

<style scoped>
.specimen-page {
  counter-reset: specimen;
}

.specimen-heading {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 24px;
  padding: 26px 28px;
  color: #eef4ff;
  background:
    linear-gradient(115deg, rgba(11, 87, 183, 0.96), rgba(18, 48, 83, 0.98)),
    repeating-linear-gradient(90deg, transparent 0 39px, rgba(255,255,255,.05) 39px 40px);
  border-radius: var(--radius);
}

.specimen-heading::after {
  position: absolute;
  right: -36px;
  bottom: -64px;
  width: 190px;
  height: 190px;
  content: '';
  border: 22px solid rgba(53, 182, 212, 0.24);
  border-radius: 50%;
}

.specimen-heading .eyebrow {
  color: #9edff0;
}

.specimen-heading h1 {
  margin: 6px 0 0;
  font-size: clamp(28px, 4vw, 48px);
  letter-spacing: -0.045em;
}

.specimen-heading dl {
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, auto);
  gap: 1px;
  margin: 0;
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.specimen-heading dl div {
  min-width: 86px;
  padding: 10px 12px;
  background: rgba(10, 37, 69, 0.72);
}

.specimen-heading dt,
.specimen-heading dd {
  margin: 0;
  font: 700 9px/1.4 var(--mono);
  letter-spacing: 0.08em;
}

.specimen-heading dt { color: #8ea9c8; }
.specimen-heading dd { margin-top: 5px; color: #fff; }

.specimen-block {
  display: grid;
  gap: 16px;
  padding: 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.specimen-index {
  display: grid;
  grid-template-columns: 44px 1fr;
  align-items: start;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.specimen-index > span {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  color: #fff;
  background: var(--primary);
  font: 700 11px/1 var(--mono);
}

.specimen-index h2,
.specimen-index p {
  margin: 0;
}

.specimen-index h2 {
  color: #17212b;
  font-size: 16px;
}

.specimen-index p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
}

.state-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.state-grid :deep(.page-state) {
  min-height: 104px;
  margin: 0;
}

.chart-frame {
  border: 1px solid var(--line);
  background-image: linear-gradient(rgba(23,92,211,.025) 1px, transparent 1px);
  background-size: 100% 24px;
}

@media (max-width: 900px) {
  .specimen-heading,
  .state-grid {
    grid-template-columns: 1fr;
  }

  .specimen-heading dl {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 560px) {
  .specimen-heading,
  .specimen-block {
    padding: 16px;
  }

  .specimen-heading dl {
    grid-template-columns: 1fr;
  }

  .specimen-heading dl div {
    display: flex;
    justify-content: space-between;
    gap: 14px;
  }
}
</style>
