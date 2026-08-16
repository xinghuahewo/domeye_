import type { P1ConversationBinding } from './contracts.js'
import {
  P1_EVENT_WINDOW_TREND_CAPABILITY,
  P1_EVENT_WINDOW_TREND_EXECUTION_UNIT,
  P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY,
} from './event-window-trend.js'
import {
  P1RuntimeV2Grounder,
  P1SemanticPlanError,
  type P1GroundingNode,
  type P1SemanticPlan,
  type P1UserGoalPlan,
} from './runtime-v2-semantic.js'

export {
  P1_EVENT_WINDOW_TREND_CAPABILITY,
  P1_EVENT_WINDOW_TREND_EXECUTION_UNIT,
  P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY,
}

function isTrendGoal(
  plan: P1SemanticPlan,
  goalId: string,
): boolean {
  const goal = plan.user_goal_plan.goals.find((item) => item.goal_id === goalId)
  return goal?.entities.analysis_mode === 'event_window_trend'
}

function nextNodeNumber(nodes: P1GroundingNode[]): number {
  return nodes.reduce((maximum, node) => {
    const value = Number(node.node_id.slice('node-'.length))
    return Number.isSafeInteger(value) ? Math.max(maximum, value) : maximum
  }, 0) + 1
}

function sameStrings(left: unknown, right: unknown): boolean {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === right.length
    && left.every((item, index) => item === right[index])
}

/**
 * 在已认证的开放 UserGoalPlan 与基础 GroundingPlan 之后，机械追加受控趋势节点。
 * 模型不选择算子、Profile 或参数；只有已通过 TOOL-03 的同 publication 轨道可以
 * 进入 OP-04。基础合同仍由 P1RuntimeV2Grounder 全量校验，本类只校验增量节点。
 */
export class P1TrendAwareGrounder extends P1RuntimeV2Grounder {
  private validatingBasePlan = false

  override ground(
    userGoalPlan: P1UserGoalPlan,
    binding: P1ConversationBinding,
    eventReference: string,
  ): P1SemanticPlan {
    let plan: P1SemanticPlan
    this.validatingBasePlan = true
    try {
      plan = super.ground(userGoalPlan, binding, eventReference)
    } finally {
      this.validatingBasePlan = false
    }
    let nextNode = nextNodeNumber(plan.grounding_plan.nodes)
    for (const decision of plan.grounding_plan.decisions) {
      if (
        !isTrendGoal(plan, decision.goal_id)
        || !['supported', 'partial'].includes(decision.answerability)
      ) continue
      const seriesNode = plan.grounding_plan.nodes.find((node) =>
        node.goal_id === decision.goal_id
        && node.execution_unit === 'TOOL-03'
        && decision.node_ids.includes(node.node_id)
      )
      if (!seriesNode || !Array.isArray(seriesNode.inputs.metrics)) {
        throw new P1SemanticPlanError(
          'trend_source_node_missing',
          '事件窗口趋势目标缺少已验证的 TOOL-03 时序来源',
        )
      }
      const nodeId = `node-${nextNode++}`
      plan.grounding_plan.nodes.push({
        node_id: nodeId,
        goal_id: decision.goal_id,
        execution_unit: P1_EVENT_WINDOW_TREND_EXECUTION_UNIT,
        capability_ids: [P1_EVENT_WINDOW_TREND_CAPABILITY],
        inputs: {
          source_node_id: seriesNode.node_id,
          metrics: [...seriesNode.inputs.metrics],
          profile_registry_version: P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY,
        },
        input_sources: {
          source_node_id: 'tool_result',
          metrics: 'tool_result',
          profile_registry_version: 'host_registry',
        },
        depends_on: [seriesNode.node_id],
        expected_evidence_sources: ['series', 'derived'],
      })
      decision.node_ids.push(nodeId)
    }
    const errors = this.validate(plan, binding)
    if (errors.length > 0) {
      plan.grounding_plan.validation = { status: 'rejected', errors }
      throw new P1SemanticPlanError(
        'grounding_plan_rejected',
        `趋势扩展 GroundingPlan 被机器门拒绝：${errors.join('; ')}`,
      )
    }
    plan.grounding_plan.validation = { status: 'passed', errors: [] }
    return plan
  }

  override validate(
    plan: P1SemanticPlan,
    binding: P1ConversationBinding,
  ): string[] {
    if (this.validatingBasePlan) return super.validate(plan, binding)
    const extensionNodes = plan.grounding_plan.nodes.filter((node) =>
      node.execution_unit === P1_EVENT_WINDOW_TREND_EXECUTION_UNIT
    )
    const extensionIds = new Set(extensionNodes.map((node) => node.node_id))
    const basePlan = structuredClone(plan)
    basePlan.grounding_plan.nodes = basePlan.grounding_plan.nodes.filter(
      (node) => !extensionIds.has(node.node_id),
    )
    for (const decision of basePlan.grounding_plan.decisions) {
      decision.node_ids = decision.node_ids.filter(
        (nodeId) => !extensionIds.has(nodeId),
      )
    }
    const errors = super.validate(basePlan, binding)
    const nodeById = new Map(
      plan.grounding_plan.nodes.map((node) => [node.node_id, node]),
    )

    for (const decision of plan.grounding_plan.decisions) {
      const expected = isTrendGoal(plan, decision.goal_id)
        && ['supported', 'partial'].includes(decision.answerability)
      const nodes = extensionNodes.filter((node) =>
        node.goal_id === decision.goal_id
      )
      if (expected !== (nodes.length === 1)) {
        errors.push('TREND-GND-01:trend_goal_node_not_closed')
      }
    }

    for (const node of extensionNodes) {
      const sourceNodeId = node.inputs.source_node_id
      const sourceNode = typeof sourceNodeId === 'string'
        ? nodeById.get(sourceNodeId)
        : undefined
      const exactInputKeys = [
        'metrics', 'profile_registry_version', 'source_node_id',
      ]
      const exactSourceKeys = [
        'metrics', 'profile_registry_version', 'source_node_id',
      ]
      if (
        node.capability_ids.length !== 1
        || node.capability_ids[0] !== P1_EVENT_WINDOW_TREND_CAPABILITY
        || Object.keys(node.inputs).sort().join(',') !== exactInputKeys.join(',')
        || Object.keys(node.input_sources).sort().join(',')
          !== exactSourceKeys.join(',')
        || node.inputs.profile_registry_version
          !== P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY
        || node.input_sources.source_node_id !== 'tool_result'
        || node.input_sources.metrics !== 'tool_result'
        || node.input_sources.profile_registry_version !== 'host_registry'
        || node.depends_on.length !== 1
        || node.depends_on[0] !== sourceNodeId
        || !sameStrings(node.expected_evidence_sources, ['series', 'derived'])
      ) {
        errors.push('TREND-GND-02:trend_node_contract_invalid')
        continue
      }
      if (
        !sourceNode
        || sourceNode.execution_unit !== 'TOOL-03'
        || sourceNode.goal_id !== node.goal_id
        || !sameStrings(node.inputs.metrics, sourceNode.inputs.metrics)
      ) {
        errors.push('TREND-GND-03:trend_source_not_closed')
      }
    }
    return [...new Set(errors)]
  }
}
