<script setup lang="ts">
import { computed, ref } from 'vue'

import type {
  CountryOutageEvidenceNode,
  CountryOutageTrendProduct,
} from '@/types/api'

defineOptions({ name: 'CountryOutageTrendAnalysis' })

const props = defineProps<{
  product: CountryOutageTrendProduct
}>()

const selectedClaimId = ref(props.product.claim_ids[0] ?? '')

const nodesById = computed(() => new Map(
  props.product.evidence_graph.nodes.map((node) => [node.node_id, node]),
))
const claims = computed(() => props.product.claim_ids
  .map((id) => nodesById.value.get(id))
  .filter((node): node is CountryOutageEvidenceNode => node?.node_type === 'Claim'))
const selectedClaim = computed(() => (
  claims.value.find((claim) => claim.node_id === selectedClaimId.value)
  ?? claims.value[0]
))
const evidenceNodes = computed(() => references(selectedClaim.value?.evidence_refs))
const limitationNodes = computed(() => references(selectedClaim.value?.limitation_refs))
const unknownNodes = computed(() => references(selectedClaim.value?.unknown_refs))

const phases = computed(() => props.product.profile.analysis.phases)
const facts = computed(() => props.product.profile.analysis.derived_facts)

function references(ids: string[] | undefined) {
  return (ids ?? [])
    .map((id) => nodesById.value.get(id))
    .filter((node): node is CountryOutageEvidenceNode => Boolean(node))
}

function text(value: unknown, fallback = '未知') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function phaseValue(phase: Record<string, unknown>, key: string) {
  return text(phase[key])
}

function factValue(fact: Record<string, unknown>, key: string) {
  return fact[key]
}

function formatFact(fact: Record<string, unknown>) {
  const value = factValue(fact, 'value')
  const unit = text(factValue(fact, 'unit'), '')
  return `${text(value)} ${unit}`.trim()
}
</script>

<template>
  <section class="trend-analysis" aria-labelledby="trend-analysis-title">
    <header class="trend-heading">
      <div>
        <p class="trend-kicker">DETERMINISTIC TREND PRODUCT</p>
        <h2 id="trend-analysis-title">窗口趋势画像与证据导航</h2>
        <p>
          下面的阶段、数字和结论均来自同一冻结制品；点击结论可下钻证据、限制与未知。
        </p>
      </div>
      <dl class="identity-grid">
        <div><dt>PRODUCT</dt><dd>{{ product.product_id }}</dd></div>
        <div><dt>PUBLICATION</dt><dd>{{ product.snapshot.publication_id }}</dd></div>
        <div><dt>REVISION</dt><dd>{{ product.snapshot.revision }}</dd></div>
        <div><dt>DATA THROUGH</dt><dd>{{ product.snapshot.data_through }}</dd></div>
      </dl>
    </header>

    <div class="quality-strip" aria-label="趋势质量与人口">
      <article>
        <span>质量</span>
        <strong>{{ product.profile.quality.status }}</strong>
        <small>
          {{ product.profile.quality.observed_slot_count }} /
          {{ product.profile.quality.expected_slot_count }} 槽已观测
        </small>
      </article>
      <article>
        <span>指标</span>
        <strong>{{ product.profile.metric.label }}</strong>
        <small>{{ product.profile.metric.statistical_population }}</small>
      </article>
      <article>
        <span>固定分母</span>
        <strong>{{ product.profile.metric.denominator.value }}</strong>
        <small>{{ product.profile.metric.denominator.statistical_population }}</small>
      </article>
      <article>
        <span>基线语义</span>
        <strong>{{ product.profile.baseline.type }}</strong>
        <small>不是历史正常带</small>
      </article>
    </div>

    <div class="claim-layout">
      <nav class="claim-list" aria-label="确定性趋势结论">
        <button
          v-for="(claim, index) in claims"
          :key="claim.node_id"
          type="button"
          :class="{ active: claim.node_id === selectedClaim?.node_id }"
          @click="selectedClaimId = claim.node_id"
        >
          <b>{{ String(index + 1).padStart(2, '0') }}</b>
          <span>{{ claim.text }}</span>
        </button>
      </nav>

      <article v-if="selectedClaim" class="evidence-card">
        <p class="claim-level">RRC25 CONTROL-PLANE CLAIM</p>
        <h3>{{ selectedClaim.text }}</h3>

        <div class="evidence-columns">
          <section>
            <h4>Evidence</h4>
            <div v-for="node in evidenceNodes" :key="node.node_id" class="node-line">
              <strong>{{ node.label || node.evidence_kind }}</strong>
              <code>{{ node.node_id }}</code>
              <p v-if="node.source_refs?.length">
                {{ node.source_refs.slice(0, 3).join(' · ') }}
              </p>
            </div>
          </section>
          <section>
            <h4>Limitation</h4>
            <p v-for="node in limitationNodes" :key="node.node_id">
              {{ node.text }}
            </p>
          </section>
          <section>
            <h4>Unknown</h4>
            <p v-for="node in unknownNodes" :key="node.node_id">
              {{ node.text }}
            </p>
          </section>
        </div>
      </article>
    </div>

    <div class="analysis-ledger">
      <section>
        <header><span>阶段序列</span><b>{{ phases.length }} PHASES</b></header>
        <ol>
          <li v-for="phase in phases" :key="phaseValue(phase, 'phase_id')">
            <b>{{ phaseValue(phase, 'kind') }}</b>
            <span>
              槽 {{ phaseValue(phase, 'start_slot_index') }}–{{ phaseValue(phase, 'end_slot_index') }}
            </span>
            <small>
              {{ phaseValue(phase, 'start_value') }} → {{ phaseValue(phase, 'end_value') }}
            </small>
          </li>
        </ol>
      </section>
      <section>
        <header><span>窗口账本</span><b>RECOMPUTABLE</b></header>
        <dl>
          <div v-for="fact in facts" :key="text(factValue(fact, 'fact_id'))">
            <dt>{{ text(factValue(fact, 'metric')) }}</dt>
            <dd>{{ formatFact(fact) }}</dd>
            <small>{{ text(factValue(fact, 'formula')) }}</small>
          </div>
        </dl>
      </section>
    </div>

    <footer class="trend-footer">
      <span>同制品输出面：{{ product.render_contract.surfaces.join(' · ') }}</span>
      <code>{{ product.graph_id }}</code>
      <strong>不包含原因、攻击、用户影响、责任或窗口外完全恢复判断</strong>
    </footer>
  </section>
</template>

<style scoped>
.trend-analysis {
  padding: 28px;
  border: 1px solid #bfd4e4;
  border-radius: 18px;
  background: linear-gradient(145deg, #f7fbfe 0%, #eef5f9 100%);
  color: #173247;
}

.trend-heading,
.claim-layout,
.analysis-ledger {
  display: grid;
  gap: 22px;
}

.trend-heading { grid-template-columns: minmax(0, 1.2fr) minmax(300px, .8fr); }
.trend-kicker,
.claim-level { color: #0b6987; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
.trend-heading h2 { margin: 5px 0 8px; font-size: clamp(24px, 3vw, 36px); }
.trend-heading p { margin: 0; color: #536b7b; }
.identity-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0; background: #c9dbe7; }
.identity-grid div { min-width: 0; padding: 10px 12px; background: #fff; }
.identity-grid dt { color: #6e8492; font-size: 10px; letter-spacing: .08em; }
.identity-grid dd { overflow: hidden; margin: 4px 0 0; font: 600 11px/1.4 ui-monospace, monospace; text-overflow: ellipsis; white-space: nowrap; }

.quality-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 22px 0; }
.quality-strip article { padding: 14px; border-left: 3px solid #1d7895; background: #fff; }
.quality-strip span, .quality-strip small { display: block; color: #6a7e8b; font-size: 11px; }
.quality-strip strong { display: block; margin: 5px 0; }

.claim-layout { grid-template-columns: minmax(260px, .72fr) minmax(0, 1.28fr); }
.claim-list { display: flex; flex-direction: column; gap: 7px; }
.claim-list button { display: grid; grid-template-columns: 28px 1fr; gap: 8px; width: 100%; padding: 11px; border: 1px solid #cfdee7; border-radius: 8px; background: #fff; color: inherit; text-align: left; cursor: pointer; }
.claim-list button.active { border-color: #0b6987; box-shadow: inset 3px 0 #0b6987; }
.claim-list b { color: #0b6987; font: 700 11px ui-monospace, monospace; }
.claim-list span { font-size: 13px; line-height: 1.55; }
.evidence-card { padding: 20px; border-radius: 12px; background: #173247; color: #f4f8fa; }
.evidence-card h3 { margin: 6px 0 18px; font-size: 20px; line-height: 1.55; }
.evidence-columns { display: grid; grid-template-columns: 1.1fr 1fr 1fr; gap: 14px; }
.evidence-columns section { padding: 12px; border: 1px solid #426072; border-radius: 8px; }
.evidence-columns h4 { margin: 0 0 10px; color: #8ad0dd; font-size: 12px; text-transform: uppercase; }
.evidence-columns p { margin: 7px 0; color: #d6e1e7; font-size: 12px; line-height: 1.5; }
.node-line code { display: block; overflow-wrap: anywhere; margin-top: 6px; color: #a9c4d0; font-size: 10px; }

.analysis-ledger { grid-template-columns: 1fr 1fr; margin-top: 22px; }
.analysis-ledger > section { padding: 18px; border: 1px solid #cfdee7; border-radius: 10px; background: #fff; }
.analysis-ledger header { display: flex; justify-content: space-between; margin-bottom: 12px; }
.analysis-ledger header b { color: #0b6987; font: 700 10px ui-monospace, monospace; }
.analysis-ledger ol { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.analysis-ledger li { display: grid; grid-template-columns: minmax(90px, auto) 1fr auto; gap: 10px; padding: 9px; background: #eef5f9; font-size: 12px; }
.analysis-ledger dl { display: grid; gap: 8px; margin: 0; }
.analysis-ledger dl div { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding-bottom: 8px; border-bottom: 1px solid #e1ebf1; }
.analysis-ledger dt { font-size: 12px; }
.analysis-ledger dd { margin: 0; font-weight: 800; }
.analysis-ledger dl small { grid-column: 1 / -1; color: #718692; font: 10px ui-monospace, monospace; }
.trend-footer { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 20px; padding-top: 16px; border-top: 1px solid #bfd4e4; color: #617785; font-size: 11px; }
.trend-footer code { overflow-wrap: anywhere; }
.trend-footer strong { color: #8a4a2a; }

@media (max-width: 900px) {
  .trend-heading,
  .claim-layout,
  .analysis-ledger { grid-template-columns: 1fr; }
  .quality-strip { grid-template-columns: repeat(2, 1fr); }
  .evidence-columns { grid-template-columns: 1fr; }
}
</style>
