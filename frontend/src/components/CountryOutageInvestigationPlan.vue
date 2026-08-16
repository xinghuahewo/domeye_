<script setup lang="ts">
import type {
  CountryOutageInvestigationNode,
  CountryOutageInvestigationPlan,
} from '@/types/api'

const emit = defineEmits<{
  select: [node: CountryOutageInvestigationNode]
  cancel: [node: CountryOutageInvestigationNode]
  rerun: [node: CountryOutageInvestigationNode]
}>()

const nodeStateLabels: Record<CountryOutageInvestigationNode['state'], string> = {
  pending: '等待',
  ready: '就绪',
  running: '执行中',
  prepared: '待提交',
  passed: '已通过',
  committed: '已提交',
  reused: '摘要复用',
  failed: '失败',
  cancelled: '已取消',
  skipped_dependency_failed: '依赖失败，已跳过',
}

function planNode(nodeId: string) {
  return props.plan.nodes.find((item) => item.node_id === nodeId)
}

const props = defineProps<{
  plan: CountryOutageInvestigationPlan
  nodes: CountryOutageInvestigationNode[]
  selectedNodeId: string
  busy: boolean
}>()
</script>

<template>
  <section class="investigation-panel" aria-labelledby="investigation-plan-title">
    <header class="investigation-panel__heading">
      <div>
        <p>VISIBLE STATIC DAG</p>
        <h2 id="investigation-plan-title">调查计划与节点</h2>
      </div>
      <span>{{ plan.plan_state }} · REV {{ plan.plan_revision }}</span>
    </header>

    <ol class="investigation-node-list">
      <li
        v-for="node in nodes"
        :key="`${node.node_id}:${node.execution_revision}`"
        :class="['investigation-node', { 'is-selected': selectedNodeId === node.node_id }]"
      >
        <button class="investigation-node__select" type="button" @click="emit('select', node)">
          <span>{{ planNode(node.node_id)?.unit_id || node.node_id }}</span>
          <strong>{{ node.node_id }}</strong>
          <small>
            {{ nodeStateLabels[node.state] }} · NODE REV {{ node.execution_revision }}
          </small>
          <small v-if="planNode(node.node_id)?.depends_on.length">
            依赖 {{ planNode(node.node_id)?.depends_on.join('、') }}
          </small>
          <small v-else>无上游依赖</small>
        </button>
        <div class="investigation-node__actions">
          <button
            type="button"
            :disabled="busy || !['pending', 'ready', 'running', 'prepared'].includes(node.state)"
            @click="emit('cancel', node)"
          >
            取消节点
          </button>
          <button
            type="button"
            :disabled="busy || !['committed', 'passed', 'reused', 'failed', 'cancelled'].includes(node.state)"
            @click="emit('rerun', node)"
          >
            单步重跑
          </button>
        </div>
      </li>
    </ol>

    <aside class="investigation-deferred" aria-label="P2.1 延期能力">
      <strong>P2.1 延期</strong>
      <p>不执行 PLAN-CAP-02、TOOL-13、OP-34，也不按 ResultSet 成员隐式展开子计划。</p>
    </aside>
  </section>
</template>

<style scoped>
.investigation-panel { border: 1px solid #d9e0ea; background: #fff; padding: 1.25rem; }
.investigation-panel__heading { display: flex; justify-content: space-between; gap: 1rem; align-items: start; }
.investigation-panel__heading p { margin: 0; color: #68758a; font-size: .72rem; letter-spacing: .12em; }
.investigation-panel__heading h2 { margin: .2rem 0 0; }
.investigation-panel__heading > span { color: #34516f; font: 600 .75rem ui-monospace, monospace; }
.investigation-node-list { display: grid; gap: .75rem; padding: 0; list-style: none; }
.investigation-node { border: 1px solid #dfe5ec; display: grid; grid-template-columns: 1fr auto; }
.investigation-node.is-selected { border-color: #275f8f; box-shadow: inset 3px 0 #275f8f; }
.investigation-node__select { display: grid; gap: .25rem; padding: .9rem; border: 0; text-align: left; background: transparent; color: inherit; }
.investigation-node__select span, .investigation-node__select small { color: #66758a; }
.investigation-node__actions { display: flex; gap: .4rem; align-items: center; padding: .75rem; }
.investigation-node__actions button { padding: .45rem .65rem; }
.investigation-deferred { border-left: 3px solid #b66a24; padding: .7rem .9rem; background: #fff8ef; }
.investigation-deferred p { margin: .25rem 0 0; }
@media (max-width: 720px) {
  .investigation-node { grid-template-columns: 1fr; }
  .investigation-node__actions { border-top: 1px solid #e7ebf0; }
}
</style>
