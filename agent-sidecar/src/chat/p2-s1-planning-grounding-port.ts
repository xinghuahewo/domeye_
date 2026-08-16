import {
  P2S1W5ContractError,
  p2S1W5AssertDigest,
  p2S1W5AssertNonempty,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  type P2S1Json,
  type P2S1TeacherSemanticPlan,
  type P2S1W5ModelRunReceipt,
  type P2S1W5TrustedReplayFixture,
} from './p2-s1-composition-contracts.js'
import {
  P2S1W5CallBudget,
  runP2S1W5ModelPhase,
  type P2S1W5InjectedModelPort,
  type P2S1W5TrustedFixtureCatalog,
} from './p2-s1-model-runner.js'
import {
  groundP2S1TeacherSemanticPlan,
  validateP2S1TeacherSemanticPlan,
} from './p2-s1-teacher-plan-grounder.js'

export const P2S1_W5_FULL_INVESTIGATION_PLAN_SCHEMA_SHA256 =
  '949b8dcb10a4c95ea6060789d174ca6c37277720724a67cf228f15be58ed5b07'

export interface P2S1W5PlanningBindingSummary {
  question_id: string
  question_digest: string
  incident_id: string
  publication_id: string
  publication_revision: number
  publication_digest: string
  collector_id: 'rrc25'
  cohort_id: string
  cohort_digest: string
  window_start_utc: string
  window_end_utc: string
  data_through_utc: string
  finality: 'event_end_unknown' | 'event_end_known'
  binding_generation: number
  registry_snapshot_id: string
  registry_snapshot_digest: string
  boundary_policy_digest: string
  prompt_version: string
  prompt_digest: string
  policy_version: string
  policy_digest: string
  teacher_model_identity_digest: string
}

export interface P2S1W5PlanningGroundingRequest {
  fixture_id: string
  goal: string
  goal_digest: string
  binding_summary: P2S1W5PlanningBindingSummary
  binding_summary_digest: string
  idempotency_key: string
}

export interface P2S1W5SemanticPlanValidationReceipt {
  schema_version: 'country_outage_p2_s1_w5_semantic_plan_validation_receipt_v1'
  validator_id: 'country_outage_p2_s1_w5_teacher_semantic_plan_validator'
  validator_version: '1.0.0'
  fixture_digest: string
  goal_digest: string
  binding_summary_digest: string
  teacher_model_identity_digest: string
  teacher_semantic_plan_digest: string
  model_run_receipt_digest: string
  allowed_capability_ids_digest: string
  disposition: 'passed'
  receipt_digest: string
}

export type P2S1W5RecipeInputSourceKind =
  | 'trusted_fixture_parameter'
  | 'identity'
  | 'node_result'
  | 'result_set'
  | 'operator_receipt'

export interface P2S1W5RecipeInputBindingSource {
  input_name: string
  source_kind: P2S1W5RecipeInputSourceKind
  source_ref: string
  source_digest: string
  source_artifact_digest: string | null
}

export interface P2S1W5ExecutionRecipeNode {
  node_id: string
  depends_on: string[]
  dependency_mode: 'hard' | 'soft'
  requiredness: 'required' | 'optional' | 'deferred' | 'boundary_only'
  unit_id: string
  atomic_capability_id: string
  parameters: Record<string, P2S1Json>
  input_binding_sources: P2S1W5RecipeInputBindingSource[]
}

export interface P2S1W5FrozenExecutionTemplate {
  schema_version: 'country_outage_p2_s1_w5_frozen_execution_template_v1'
  template_group_id: string
  fixture_id: string
  question_id: string
  question_digest: string
  goal_digest: string
  semantic_capability_ids: string[]
  plan_id: string
  plan_revision: number
  registry_snapshot_id: string
  registry_snapshot_digest: string
  nodes: P2S1W5ExecutionRecipeNode[]
  template_digest: string
}

export interface P2S1W5GroundedExecutionRecipe {
  schema_version: 'country_outage_p2_s1_w5_grounded_execution_recipe_v1'
  template_group_id: string
  template_group_digest: string
  fixture_id: string
  question_id: string
  question_digest: string
  goal_digest: string
  binding_summary_digest: string
  semantic_plan_digest: string
  semantic_capability_ids: string[]
  plan_id: string
  plan_revision: number
  registry_snapshot_id: string
  registry_snapshot_digest: string
  nodes: P2S1W5ExecutionRecipeNode[]
  recipe_digest: string
}

export interface P2S1W5ExecutionRecipeGroundingReceipt {
  receipt_id: string
  teacher_semantic_plan_digest: string
  grounding_plan_digest: string
  registry_snapshot_digest: string
  grounded_execution_recipe_digest: string
  grounding_plan_projection_digest: string
  disposition: 'passed'
  receipt_digest: string
}

export interface P2S1W5TrustedGroundingPlanProjection {
  schema_version: 'country_outage_p2_grounding_plan_projection_v2'
  plan_id: string
  plan_revision: number
  admitted_capability_ids: string[]
  registry_snapshot_id: string
  registry_snapshot_digest: string
  effective_teacher_required: boolean
  degraded_authorization_digest: string | null
  grounded_execution_recipe: P2S1W5GroundedExecutionRecipe
  grounding_plan_projection_digest: string
}

export interface P2S1W5PlanningGroundingResult {
  schema_version: 'country_outage_p2_s1_w5_planning_grounding_result_v1'
  disposition: 'grounded_projection' | 'planning_unavailable'
  fixture_id: string
  fixture_digest: string
  goal_digest: string
  binding_summary_digest: string
  teacher_semantic_plan: P2S1TeacherSemanticPlan | null
  teacher_plan_run_receipt: P2S1W5ModelRunReceipt
  semantic_plan_validation_receipt: P2S1W5SemanticPlanValidationReceipt | null
  host_grounding_receipt: P2S1W5ExecutionRecipeGroundingReceipt | null
  trusted_grounding_plan_projection: P2S1W5TrustedGroundingPlanProjection | null
  full_investigation_plan: {
    status: 'host_runtime_required'
    required_schema_version: 'country_outage_p2_investigation_plan_v1'
    required_schema_sha256: typeof P2S1_W5_FULL_INVESTIGATION_PLAN_SCHEMA_SHA256
    artifact_ref: null
    artifact_digest: null
    projection_is_full_plan: false
  }
  execution_boundary: {
    execution_mode: 'trusted_fixture_replay_only'
    sol_planning_attempt_count: 1
    sol_reference_attempt_count: 0
    student_attempt_count: 0
    dual_answer_flow_started: false
    external_provider_called: false
    p1_certification_reused: false
    production_runtime_integrated: false
    production_deployed: false
  }
}

interface IdempotentEntry {
  requestDigest: string
  promise: Promise<P2S1W5PlanningGroundingResult>
}

function sameValue(left: unknown, right: unknown): boolean {
  return p2S1W5Digest(left) === p2S1W5Digest(right)
}

const FROZEN_EXECUTION_UNIT_CAPABILITIES: Readonly<Record<string, string>> = Object.freeze({
  'GATE-01': 'validate.identity',
  'GATE-02': 'validate.evidence_refs',
  'GATE-03': 'validate.result_completeness',
  'TOOL-07': 'read.fixed_cohort_members',
  'TOOL-11': 'read.materialized_route_states_at_time',
  'OP-29': 'time.evidence_relation',
  'OP-37': 'classify.evidence_consistency',
  'BOUNDARY-01': 'respond.boundary',
})

function jsonObject(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1W5ContractError('execution_recipe_invalid', `${label} 必须是对象`)
  }
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[], label: string): void {
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new P2S1W5ContractError('execution_recipe_invalid', `${label} 字段集合不符合冻结合同`)
  }
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item)) {
    throw new P2S1W5ContractError('execution_recipe_invalid', `${label} 必须是非空字符串数组`)
  }
  if (new Set(value).size !== value.length) {
    throw new P2S1W5ContractError('execution_recipe_invalid', `${label} 不得重复`)
  }
  return [...value]
}

function validateRecipeNodes(value: unknown): P2S1W5ExecutionRecipeNode[] {
  if (!Array.isArray(value) || value.length < 1) {
    throw new P2S1W5ContractError('execution_recipe_invalid', 'recipe 至少包含一个节点')
  }
  const nodes = value.map((raw, index) => {
    const node = jsonObject(raw, `nodes[${index}]`)
    exactKeys(node, [
      'node_id', 'depends_on', 'dependency_mode', 'requiredness', 'unit_id',
      'atomic_capability_id', 'parameters', 'input_binding_sources',
    ], `nodes[${index}]`)
    p2S1W5AssertNonempty(node.node_id, `nodes[${index}].node_id`)
    p2S1W5AssertNonempty(node.unit_id, `nodes[${index}].unit_id`)
    p2S1W5AssertNonempty(node.atomic_capability_id, `nodes[${index}].atomic_capability_id`)
    const expectedCapability = FROZEN_EXECUTION_UNIT_CAPABILITIES[node.unit_id]
    if (!expectedCapability) {
      throw new P2S1W5ContractError('execution_recipe_ghost_unit', `未冻结 execution unit：${node.unit_id}`)
    }
    if (node.atomic_capability_id !== expectedCapability) {
      throw new P2S1W5ContractError('execution_recipe_capability_drift', `${node.unit_id} atomic capability 漂移`)
    }
    if (!['hard', 'soft'].includes(String(node.dependency_mode))) {
      throw new P2S1W5ContractError('execution_recipe_invalid', 'dependency_mode 无效')
    }
    if (!['required', 'optional', 'deferred', 'boundary_only'].includes(String(node.requiredness))) {
      throw new P2S1W5ContractError('execution_recipe_invalid', 'requiredness 无效')
    }
    const parameters = jsonObject(node.parameters, `nodes[${index}].parameters`) as Record<string, P2S1Json>
    if (!Array.isArray(node.input_binding_sources)) {
      throw new P2S1W5ContractError('execution_recipe_invalid', 'input_binding_sources 必须是数组')
    }
    const sources = node.input_binding_sources.map((sourceRaw, sourceIndex) => {
      const source = jsonObject(sourceRaw, `nodes[${index}].input_binding_sources[${sourceIndex}]`)
      exactKeys(source, [
        'input_name', 'source_kind', 'source_ref', 'source_digest', 'source_artifact_digest',
      ], `nodes[${index}].input_binding_sources[${sourceIndex}]`)
      p2S1W5AssertNonempty(source.input_name, 'input_name')
      p2S1W5AssertNonempty(source.source_ref, 'source_ref')
      p2S1W5AssertDigest(source.source_digest, 'source_digest')
      if (![
        'trusted_fixture_parameter', 'identity', 'node_result', 'result_set', 'operator_receipt',
      ].includes(String(source.source_kind))) {
        throw new P2S1W5ContractError('execution_recipe_binding_source_drift', 'input binding source_kind 无效')
      }
      const parameter = parameters[source.input_name as string]
      if (parameter === undefined) {
        throw new P2S1W5ContractError('execution_recipe_parameter_binding_mismatch', 'binding 未绑定已存在参数')
      }
      const expectedSourceDigest = p2S1W5Digest({
        input_name: source.input_name,
        source_kind: source.source_kind,
        source_ref: source.source_ref,
        bound_parameter_value: parameter,
      })
      if (source.source_digest !== expectedSourceDigest) {
        throw new P2S1W5ContractError('execution_recipe_binding_source_drift', 'input binding source 摘要漂移')
      }
      const requiresArtifact = ['node_result', 'result_set', 'operator_receipt'].includes(String(source.source_kind))
      if (requiresArtifact !== (typeof source.source_artifact_digest === 'string')) {
        throw new P2S1W5ContractError('execution_recipe_binding_source_drift', 'source_artifact_digest 与来源类型不闭合')
      }
      if (typeof source.source_artifact_digest === 'string') {
        p2S1W5AssertDigest(source.source_artifact_digest, 'source_artifact_digest')
      } else if (source.source_artifact_digest !== null) {
        throw new P2S1W5ContractError('execution_recipe_binding_source_drift', 'source_artifact_digest 必须为摘要或 null')
      }
      return source as unknown as P2S1W5RecipeInputBindingSource
    })
    const sourceNames = sources.map((source) => source.input_name)
    if (new Set(sourceNames).size !== sourceNames.length
      || Object.keys(parameters).length !== sourceNames.length
      || Object.keys(parameters).some((name) => !sourceNames.includes(name))) {
      throw new P2S1W5ContractError('execution_recipe_parameter_binding_mismatch', '参数与 binding source 必须一一对应')
    }
    return {
      node_id: node.node_id as string,
      depends_on: stringArray(node.depends_on, 'depends_on'),
      dependency_mode: node.dependency_mode as 'hard' | 'soft',
      requiredness: node.requiredness as P2S1W5ExecutionRecipeNode['requiredness'],
      unit_id: node.unit_id as string,
      atomic_capability_id: node.atomic_capability_id as string,
      parameters: p2S1W5Clone(parameters),
      input_binding_sources: p2S1W5Clone(sources),
    }
  })
  const ids = nodes.map((node) => node.node_id)
  if (new Set(ids).size !== ids.length) {
    throw new P2S1W5ContractError('execution_recipe_node_drift', 'node_id 不得重复')
  }
  const seen = new Set<string>()
  for (const node of nodes) {
    if (node.depends_on.some((dependency) => !seen.has(dependency))) {
      throw new P2S1W5ContractError('execution_recipe_node_drift', '节点依赖必须引用前序节点并形成闭合 DAG')
    }
    seen.add(node.node_id)
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(nodes))
}

function validateRecipeSourceClosure(
  nodes: readonly P2S1W5ExecutionRecipeNode[],
  fixtureId: string,
): void {
  const byId = new Map(nodes.map((node) => [node.node_id, node]))
  const ancestors = (nodeId: string, seen = new Set<string>()): Set<string> => {
    const node = byId.get(nodeId)
    if (!node) return seen
    for (const dependency of node.depends_on) {
      if (!seen.has(dependency)) {
        seen.add(dependency)
        ancestors(dependency, seen)
      }
    }
    return seen
  }
  for (const node of nodes) {
    const allowedAncestors = ancestors(node.node_id)
    for (const source of node.input_binding_sources) {
      if (source.source_kind === 'trusted_fixture_parameter') {
        const expected = `fixture:${fixtureId}:parameter:${node.node_id}:${source.input_name}`
        if (source.source_ref !== expected) {
          throw new P2S1W5ContractError('execution_recipe_binding_source_drift', 'fixture 参数来源未绑定同一 fixture/node/input')
        }
      }
      if (['node_result', 'result_set', 'operator_receipt'].includes(source.source_kind)
        && !allowedAncestors.has(source.source_ref)) {
        throw new P2S1W5ContractError('execution_recipe_binding_source_drift', '节点结果来源必须是当前节点祖先')
      }
    }
  }
}

export function validateP2S1W5FrozenExecutionTemplate(options: {
  value: unknown
  fixture: P2S1W5TrustedReplayFixture
}): P2S1W5FrozenExecutionTemplate {
  const value = jsonObject(options.value, 'frozen_execution_template')
  exactKeys(value, [
    'schema_version', 'template_group_id', 'fixture_id', 'question_id', 'question_digest',
    'goal_digest', 'semantic_capability_ids', 'plan_id', 'plan_revision',
    'registry_snapshot_id', 'registry_snapshot_digest', 'nodes', 'template_digest',
  ], 'frozen_execution_template')
  if (value.schema_version !== 'country_outage_p2_s1_w5_frozen_execution_template_v1') {
    throw new P2S1W5ContractError('execution_recipe_invalid', 'template schema_version 无效')
  }
  p2S1W5AssertNonempty(value.template_group_id, 'template_group_id')
  p2S1W5AssertDigest(value.template_digest, 'template_digest')
  const fixture = options.fixture
  if (
    value.fixture_id !== fixture.fixture_id
    || value.question_id !== fixture.binding.question_id
    || value.question_digest !== fixture.binding.question_digest
    || value.goal_digest !== fixture.binding.goal_digest
    || value.plan_id !== fixture.grounding_plan.plan_id
    || value.plan_revision !== fixture.grounding_plan.plan_revision
    || value.registry_snapshot_id !== fixture.grounding_plan.registry_snapshot_id
    || value.registry_snapshot_digest !== fixture.grounding_plan.registry_snapshot_digest
  ) throw new P2S1W5ContractError('execution_recipe_template_binding_drift', 'execution template 与 fixture/Plan/Registry 绑定漂移')
  const capabilities = stringArray(value.semantic_capability_ids, 'semantic_capability_ids')
  if (!sameValue(capabilities, fixture.allowed_capability_ids)) {
    throw new P2S1W5ContractError('execution_recipe_capability_drift', 'template semantic capability 集合漂移')
  }
  const nodes = validateRecipeNodes(value.nodes)
  validateRecipeSourceClosure(nodes, fixture.fixture_id)
  if (value.template_digest !== p2S1W5DigestWithout(value, 'template_digest')) {
    throw new P2S1W5ContractError('execution_recipe_template_digest_drift', 'execution template 摘要漂移')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone({ ...value, semantic_capability_ids: capabilities, nodes } as unknown as P2S1W5FrozenExecutionTemplate))
}

function fixtureExecutionTemplate(fixture: P2S1W5TrustedReplayFixture): P2S1W5FrozenExecutionTemplate {
  const value = (fixture as unknown as { frozen_execution_template?: unknown }).frozen_execution_template
  if (!value) {
    throw new P2S1W5ContractError(
      'planning_grounding_incomplete',
      '受信 fixture 缺少 Q/goal 对应的冻结 execution template',
    )
  }
  return validateP2S1W5FrozenExecutionTemplate({ value, fixture })
}

export function p2S1W5PlanningBindingSummary(
  fixture: P2S1W5TrustedReplayFixture,
): P2S1W5PlanningBindingSummary {
  return p2S1W5DeepFreeze({
    question_id: fixture.binding.question_id,
    question_digest: fixture.binding.question_digest,
    incident_id: fixture.binding.incident_id,
    publication_id: fixture.binding.publication_id,
    publication_revision: fixture.binding.publication_revision,
    publication_digest: fixture.binding.publication_digest,
    collector_id: 'rrc25',
    cohort_id: fixture.binding.cohort_id,
    cohort_digest: fixture.binding.cohort_digest,
    window_start_utc: fixture.binding.window_start_utc,
    window_end_utc: fixture.binding.window_end_utc,
    data_through_utc: fixture.binding.data_through_utc,
    finality: fixture.binding.finality,
    binding_generation: fixture.binding.binding_generation,
    registry_snapshot_id: fixture.grounding_plan.registry_snapshot_id,
    registry_snapshot_digest: fixture.grounding_plan.registry_snapshot_digest,
    boundary_policy_digest: fixture.binding.boundary_policy_digest,
    prompt_version: fixture.binding.prompt_version,
    prompt_digest: fixture.binding.prompt_digest,
    policy_version: fixture.binding.policy_version,
    policy_digest: fixture.binding.policy_digest,
    teacher_model_identity_digest: fixture.teacher_identity.identity_digest,
  })
}

function fullPlanBoundary(): P2S1W5PlanningGroundingResult['full_investigation_plan'] {
  return {
    status: 'host_runtime_required',
    required_schema_version: 'country_outage_p2_investigation_plan_v1',
    required_schema_sha256: P2S1_W5_FULL_INVESTIGATION_PLAN_SCHEMA_SHA256,
    artifact_ref: null,
    artifact_digest: null,
    projection_is_full_plan: false,
  }
}

function executionBoundary(): P2S1W5PlanningGroundingResult['execution_boundary'] {
  return {
    execution_mode: 'trusted_fixture_replay_only',
    sol_planning_attempt_count: 1,
    sol_reference_attempt_count: 0,
    student_attempt_count: 0,
    dual_answer_flow_started: false,
    external_provider_called: false,
    p1_certification_reused: false,
    production_runtime_integrated: false,
    production_deployed: false,
  }
}

function validateRequestAgainstFixture(
  request: P2S1W5PlanningGroundingRequest,
  fixture: P2S1W5TrustedReplayFixture,
): P2S1W5PlanningBindingSummary {
  p2S1W5AssertNonempty(request.goal, 'goal')
  p2S1W5AssertDigest(request.goal_digest, 'goal_digest')
  p2S1W5AssertDigest(request.binding_summary_digest, 'binding_summary_digest')
  if (request.goal_digest !== p2S1W5Digest(request.goal)) {
    throw new P2S1W5ContractError('goal_digest_mismatch', 'goal_digest 无法由 goal 重算')
  }
  if (request.goal !== fixture.binding.goal || request.goal_digest !== fixture.binding.goal_digest) {
    throw new P2S1W5ContractError('goal_fixture_mismatch', 'goal 未绑定受信 fixture')
  }
  if (request.binding_summary_digest !== p2S1W5Digest(request.binding_summary)) {
    throw new P2S1W5ContractError('binding_summary_digest_mismatch', 'binding summary 摘要无法重算')
  }
  const trusted = p2S1W5PlanningBindingSummary(fixture)
  if (!sameValue(request.binding_summary, trusted)) {
    throw new P2S1W5ContractError('identity_binding_fixture_mismatch', '身份或 binding summary 未绑定受信 fixture')
  }
  return trusted
}

function semanticValidationReceipt(options: {
  fixture: P2S1W5TrustedReplayFixture
  goalDigest: string
  bindingSummaryDigest: string
  semanticPlan: P2S1TeacherSemanticPlan
  modelRunReceipt: P2S1W5ModelRunReceipt
}): P2S1W5SemanticPlanValidationReceipt {
  const withoutDigest = {
    schema_version: 'country_outage_p2_s1_w5_semantic_plan_validation_receipt_v1' as const,
    validator_id: 'country_outage_p2_s1_w5_teacher_semantic_plan_validator' as const,
    validator_version: '1.0.0' as const,
    fixture_digest: options.fixture.fixture_digest,
    goal_digest: options.goalDigest,
    binding_summary_digest: options.bindingSummaryDigest,
    teacher_model_identity_digest: options.fixture.teacher_identity.identity_digest,
    teacher_semantic_plan_digest: options.semanticPlan.output_digest,
    model_run_receipt_digest: p2S1W5Digest(options.modelRunReceipt),
    allowed_capability_ids_digest: p2S1W5Digest(options.fixture.allowed_capability_ids),
    disposition: 'passed' as const,
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    receipt_digest: p2S1W5Digest(withoutDigest),
  })
}

function groundedExecutionRecipe(options: {
  fixture: P2S1W5TrustedReplayFixture
  template: P2S1W5FrozenExecutionTemplate
  semanticPlan: P2S1TeacherSemanticPlan
  bindingSummaryDigest: string
}): P2S1W5GroundedExecutionRecipe {
  const withoutDigest = {
    schema_version: 'country_outage_p2_s1_w5_grounded_execution_recipe_v1' as const,
    template_group_id: options.template.template_group_id,
    template_group_digest: options.template.template_digest,
    fixture_id: options.fixture.fixture_id,
    question_id: options.fixture.binding.question_id,
    question_digest: options.fixture.binding.question_digest,
    goal_digest: options.fixture.binding.goal_digest,
    binding_summary_digest: options.bindingSummaryDigest,
    semantic_plan_digest: options.semanticPlan.output_digest,
    semantic_capability_ids: p2S1W5Clone(options.template.semantic_capability_ids),
    plan_id: options.template.plan_id,
    plan_revision: options.template.plan_revision,
    registry_snapshot_id: options.template.registry_snapshot_id,
    registry_snapshot_digest: options.template.registry_snapshot_digest,
    nodes: p2S1W5Clone(options.template.nodes),
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    recipe_digest: p2S1W5Digest(withoutDigest),
  })
}

function groundingProjection(options: {
  plan: ReturnType<typeof groundP2S1TeacherSemanticPlan>['plan']
  recipe: P2S1W5GroundedExecutionRecipe
}): P2S1W5TrustedGroundingPlanProjection {
  const withoutDigest = {
    schema_version: 'country_outage_p2_grounding_plan_projection_v2' as const,
    plan_id: options.plan.plan_id,
    plan_revision: options.plan.plan_revision,
    admitted_capability_ids: p2S1W5Clone(options.plan.admitted_capability_ids),
    registry_snapshot_id: options.plan.registry_snapshot_id,
    registry_snapshot_digest: options.plan.registry_snapshot_digest,
    effective_teacher_required: options.plan.effective_teacher_required,
    degraded_authorization_digest: options.plan.degraded_authorization_digest,
    grounded_execution_recipe: p2S1W5Clone(options.recipe),
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    grounding_plan_projection_digest: p2S1W5Digest(withoutDigest),
  })
}

export function validateP2S1W5TrustedGroundingPlanProjection(
  raw: unknown,
): P2S1W5TrustedGroundingPlanProjection {
  const value = jsonObject(raw, 'trusted_grounding_plan_projection')
  exactKeys(value, [
    'schema_version', 'plan_id', 'plan_revision', 'admitted_capability_ids',
    'registry_snapshot_id', 'registry_snapshot_digest', 'effective_teacher_required',
    'degraded_authorization_digest', 'grounded_execution_recipe',
    'grounding_plan_projection_digest',
  ], 'trusted_grounding_plan_projection')
  if (value.schema_version !== 'country_outage_p2_grounding_plan_projection_v2') {
    throw new P2S1W5ContractError('grounding_projection_invalid', 'grounding projection schema_version 无效')
  }
  p2S1W5AssertNonempty(value.plan_id, 'projection.plan_id')
  p2S1W5AssertNonempty(value.registry_snapshot_id, 'projection.registry_snapshot_id')
  p2S1W5AssertDigest(value.registry_snapshot_digest, 'projection.registry_snapshot_digest')
  p2S1W5AssertDigest(value.grounding_plan_projection_digest, 'projection.digest')
  if (!Number.isSafeInteger(value.plan_revision) || (value.plan_revision as number) < 1) {
    throw new P2S1W5ContractError('grounding_projection_invalid', 'projection.plan_revision 无效')
  }
  const capabilities = stringArray(value.admitted_capability_ids, 'admitted_capability_ids')
  const recipe = jsonObject(value.grounded_execution_recipe, 'grounded_execution_recipe')
  exactKeys(recipe, [
    'schema_version', 'template_group_id', 'template_group_digest', 'fixture_id',
    'question_id', 'question_digest', 'goal_digest', 'binding_summary_digest',
    'semantic_plan_digest', 'semantic_capability_ids', 'plan_id', 'plan_revision',
    'registry_snapshot_id', 'registry_snapshot_digest', 'nodes', 'recipe_digest',
  ], 'grounded_execution_recipe')
  if (recipe.schema_version !== 'country_outage_p2_s1_w5_grounded_execution_recipe_v1') {
    throw new P2S1W5ContractError('execution_recipe_invalid', 'grounded recipe schema_version 无效')
  }
  for (const field of [
    'template_group_digest', 'question_digest', 'goal_digest', 'binding_summary_digest',
    'semantic_plan_digest', 'registry_snapshot_digest', 'recipe_digest',
  ] as const) p2S1W5AssertDigest(recipe[field], `recipe.${field}`)
  const recipeCapabilities = stringArray(recipe.semantic_capability_ids, 'recipe.semantic_capability_ids')
  const nodes = validateRecipeNodes(recipe.nodes)
  p2S1W5AssertNonempty(recipe.template_group_id, 'recipe.template_group_id')
  p2S1W5AssertNonempty(recipe.fixture_id, 'recipe.fixture_id')
  p2S1W5AssertNonempty(recipe.question_id, 'recipe.question_id')
  p2S1W5AssertNonempty(recipe.plan_id, 'recipe.plan_id')
  p2S1W5AssertNonempty(recipe.registry_snapshot_id, 'recipe.registry_snapshot_id')
  validateRecipeSourceClosure(nodes, recipe.fixture_id as string)
  if (
    recipe.plan_id !== value.plan_id
    || recipe.plan_revision !== value.plan_revision
    || recipe.registry_snapshot_id !== value.registry_snapshot_id
    || recipe.registry_snapshot_digest !== value.registry_snapshot_digest
    || !sameValue(recipeCapabilities, capabilities)
  ) throw new P2S1W5ContractError('grounding_projection_recipe_mismatch', 'projection 与 recipe 的 Plan/Registry/capability 绑定不一致')
  if (recipe.recipe_digest !== p2S1W5DigestWithout(recipe, 'recipe_digest')) {
    throw new P2S1W5ContractError('execution_recipe_digest_drift', 'grounded recipe 摘要漂移')
  }
  const reconstructedTemplate = {
    schema_version: 'country_outage_p2_s1_w5_frozen_execution_template_v1',
    template_group_id: recipe.template_group_id,
    fixture_id: recipe.fixture_id,
    question_id: recipe.question_id,
    question_digest: recipe.question_digest,
    goal_digest: recipe.goal_digest,
    semantic_capability_ids: recipeCapabilities,
    plan_id: recipe.plan_id,
    plan_revision: recipe.plan_revision,
    registry_snapshot_id: recipe.registry_snapshot_id,
    registry_snapshot_digest: recipe.registry_snapshot_digest,
    nodes,
  }
  if (recipe.template_group_digest !== p2S1W5Digest(reconstructedTemplate)) {
    throw new P2S1W5ContractError('execution_recipe_template_digest_drift', 'grounded recipe 未绑定冻结 template group')
  }
  if (value.grounding_plan_projection_digest !== p2S1W5DigestWithout(value, 'grounding_plan_projection_digest')) {
    throw new P2S1W5ContractError('grounding_projection_digest_drift', 'grounding projection 摘要漂移')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone({
    ...value,
    admitted_capability_ids: capabilities,
    grounded_execution_recipe: { ...recipe, semantic_capability_ids: recipeCapabilities, nodes },
  } as unknown as P2S1W5TrustedGroundingPlanProjection))
}

export function validateP2S1W5PlanningGroundingClosure(
  raw: P2S1W5PlanningGroundingResult,
): P2S1W5PlanningGroundingResult {
  if (raw.disposition === 'planning_unavailable') {
    if (
      raw.teacher_semantic_plan !== null
      || raw.semantic_plan_validation_receipt !== null
      || raw.host_grounding_receipt !== null
      || raw.trusted_grounding_plan_projection !== null
    ) throw new P2S1W5ContractError('planning_grounding_terminal_closure_drift', 'planning unavailable 终态未闭合')
    return p2S1W5DeepFreeze(p2S1W5Clone(raw))
  }
  if (
    !raw.teacher_semantic_plan
    || !raw.semantic_plan_validation_receipt
    || !raw.host_grounding_receipt
    || !raw.trusted_grounding_plan_projection
  ) throw new P2S1W5ContractError('planning_grounding_terminal_closure_drift', 'grounded projection 缺少必需 artifact/receipt')
  const projection = validateP2S1W5TrustedGroundingPlanProjection(raw.trusted_grounding_plan_projection)
  const recipe = projection.grounded_execution_recipe
  const receipt = raw.host_grounding_receipt
  const semanticCapabilities = raw.teacher_semantic_plan.subgoals.map((item) => item.capability_id)
  if (
    raw.goal_digest !== recipe.goal_digest
    || raw.binding_summary_digest !== recipe.binding_summary_digest
    || raw.fixture_id !== recipe.fixture_id
    || raw.teacher_semantic_plan.output_digest !== recipe.semantic_plan_digest
    || raw.semantic_plan_validation_receipt.teacher_semantic_plan_digest !== recipe.semantic_plan_digest
    || raw.semantic_plan_validation_receipt.goal_digest !== raw.goal_digest
    || raw.semantic_plan_validation_receipt.binding_summary_digest !== raw.binding_summary_digest
    || raw.semantic_plan_validation_receipt.receipt_digest !== p2S1W5DigestWithout(
      raw.semantic_plan_validation_receipt as unknown as Record<string, unknown>,
      'receipt_digest',
    )
    || !sameValue(semanticCapabilities, projection.admitted_capability_ids)
    || receipt.teacher_semantic_plan_digest !== recipe.semantic_plan_digest
    || receipt.registry_snapshot_digest !== projection.registry_snapshot_digest
    || receipt.grounded_execution_recipe_digest !== recipe.recipe_digest
    || receipt.grounding_plan_projection_digest !== projection.grounding_plan_projection_digest
    || receipt.receipt_digest !== p2S1W5DigestWithout(
      receipt as unknown as Record<string, unknown>,
      'receipt_digest',
    )
  ) throw new P2S1W5ContractError('planning_grounding_closure_drift', 'planning result、recipe、projection 与回执未闭合')
  return p2S1W5DeepFreeze(p2S1W5Clone({
    ...raw,
    trusted_grounding_plan_projection: projection,
  }))
}

function executionRecipeGroundingReceipt(options: {
  base: ReturnType<typeof groundP2S1TeacherSemanticPlan>['receipt']
  projection: P2S1W5TrustedGroundingPlanProjection
}): P2S1W5ExecutionRecipeGroundingReceipt {
  const withoutDigest = {
    receipt_id: options.base.receipt_id,
    teacher_semantic_plan_digest: options.base.teacher_semantic_plan_digest,
    grounding_plan_digest: options.base.grounding_plan_digest,
    registry_snapshot_digest: options.base.registry_snapshot_digest,
    grounded_execution_recipe_digest: options.projection.grounded_execution_recipe.recipe_digest,
    grounding_plan_projection_digest: options.projection.grounding_plan_projection_digest,
    disposition: 'passed' as const,
  }
  return p2S1W5DeepFreeze({ ...withoutDigest, receipt_digest: p2S1W5Digest(withoutDigest) })
}

/**
 * 该端口只产生语义计划与 Host grounding projection。
 * 完整 InvestigationPlan 必须由 Python Host 使用冻结 Schema、受信 Registry、权限与预算重新落地并准入。
 */
export class P2S1W5PlanningGroundingRuntime {
  readonly #requests = new Map<string, IdempotentEntry>()

  constructor(private readonly options: {
    fixtures: P2S1W5TrustedFixtureCatalog
    modelPort: P2S1W5InjectedModelPort
  }) {
    if (options.modelPort.mode !== 'trusted_fixture_replay') {
      throw new P2S1W5ContractError('external_provider_forbidden', 'planning/grounding 端口只允许 fixture replay')
    }
  }

  async run(request: P2S1W5PlanningGroundingRequest): Promise<P2S1W5PlanningGroundingResult> {
    p2S1W5AssertNonempty(request.fixture_id, 'fixture_id')
    p2S1W5AssertNonempty(request.idempotency_key, 'idempotency_key')
    const requestDigest = p2S1W5Digest(request)
    const existing = this.#requests.get(request.idempotency_key)
    if (existing) {
      if (existing.requestDigest !== requestDigest) {
        throw new P2S1W5ContractError('idempotency_conflict', '同一幂等键绑定了不同 planning/grounding 请求')
      }
      return existing.promise.then((value) => p2S1W5DeepFreeze(p2S1W5Clone(value)))
    }
    const promise = this.#runFresh(p2S1W5Clone(request))
    this.#requests.set(request.idempotency_key, { requestDigest, promise })
    return promise.then((value) => p2S1W5DeepFreeze(p2S1W5Clone(value)))
  }

  async #runFresh(request: P2S1W5PlanningGroundingRequest): Promise<P2S1W5PlanningGroundingResult> {
    const fixture = this.options.fixtures.resolve(request.fixture_id)
    const trustedSummary = validateRequestAgainstFixture(request, fixture)
    const executionTemplate = fixtureExecutionTemplate(fixture)
    const budget = new P2S1W5CallBudget()
    const attempt = await runP2S1W5ModelPhase({
      port: this.options.modelPort,
      budget,
      fixtureId: fixture.fixture_id,
      phase: 'sol_planning',
      identity: fixture.teacher_identity,
      sharedAnswerBindingDigest: request.binding_summary_digest,
      roleSpecificInput: {
        role: 'teacher',
        run_phase: 'sol_planning',
        goal_digest: request.goal_digest,
        binding_summary_digest: request.binding_summary_digest,
        question_digest: trustedSummary.question_digest,
        incident_id: trustedSummary.incident_id,
        publication_id: trustedSummary.publication_id,
        publication_revision: trustedSummary.publication_revision,
        collector_id: 'rrc25',
        registry_snapshot_digest: trustedSummary.registry_snapshot_digest,
        prompt_digest: trustedSummary.prompt_digest,
        policy_digest: trustedSummary.policy_digest,
      },
    })
    if (attempt.receipt.disposition !== 'completed' || attempt.output === null) {
      return p2S1W5DeepFreeze({
        schema_version: 'country_outage_p2_s1_w5_planning_grounding_result_v1',
        disposition: 'planning_unavailable',
        fixture_id: fixture.fixture_id,
        fixture_digest: fixture.fixture_digest,
        goal_digest: request.goal_digest,
        binding_summary_digest: request.binding_summary_digest,
        teacher_semantic_plan: null,
        teacher_plan_run_receipt: attempt.receipt,
        semantic_plan_validation_receipt: null,
        host_grounding_receipt: null,
        trusted_grounding_plan_projection: null,
        full_investigation_plan: fullPlanBoundary(),
        execution_boundary: executionBoundary(),
      })
    }
    const semanticPlan = validateP2S1TeacherSemanticPlan({
      value: attempt.output,
      questionId: fixture.binding.question_id,
      questionDigest: fixture.binding.question_digest,
      goalDigest: fixture.binding.goal_digest,
      allowedCapabilityIds: fixture.allowed_capability_ids,
    })
    const validationReceipt = semanticValidationReceipt({
      fixture,
      goalDigest: request.goal_digest,
      bindingSummaryDigest: request.binding_summary_digest,
      semanticPlan,
      modelRunReceipt: attempt.receipt,
    })
    const grounding = groundP2S1TeacherSemanticPlan({
      semanticPlan,
      trustedPlan: fixture.grounding_plan,
      trustedGraph: fixture.evidence_graph,
    })
    const recipe = groundedExecutionRecipe({
      fixture,
      template: executionTemplate,
      semanticPlan,
      bindingSummaryDigest: request.binding_summary_digest,
    })
    const projection = validateP2S1W5TrustedGroundingPlanProjection(groundingProjection({
      plan: grounding.plan,
      recipe,
    }))
    const groundingReceipt = executionRecipeGroundingReceipt({
      base: grounding.receipt,
      projection,
    })
    return validateP2S1W5PlanningGroundingClosure(p2S1W5DeepFreeze({
      schema_version: 'country_outage_p2_s1_w5_planning_grounding_result_v1',
      disposition: 'grounded_projection',
      fixture_id: fixture.fixture_id,
      fixture_digest: fixture.fixture_digest,
      goal_digest: request.goal_digest,
      binding_summary_digest: request.binding_summary_digest,
      teacher_semantic_plan: semanticPlan,
      teacher_plan_run_receipt: attempt.receipt,
      semantic_plan_validation_receipt: validationReceipt,
      host_grounding_receipt: groundingReceipt,
      trusted_grounding_plan_projection: projection,
      full_investigation_plan: fullPlanBoundary(),
      execution_boundary: executionBoundary(),
    }))
  }
}
