#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Errors } from '../../agent-sidecar/node_modules/typebox/build/value/index.mjs';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, '../..');
const STAGE_ROOT = path.join(
  ROOT,
  'evaluation/country-outage/p1-page-coverage/s2',
);
const RAW_ROOT = path.join(STAGE_ROOT, 'raw');
const RECEIPT_ROOT = path.join(RAW_ROOT, 'agent-receipts');
const WRAPPER_PATH = path.join(
  RECEIPT_ROOT,
  'question-explorer-results.raw.json',
);
const CANDIDATE_PATH = path.join(RAW_ROOT, 'candidate-identity.json');
const RAW_AGGREGATE_PATH = path.join(RAW_ROOT, 'raw-agent-receipts.json');
const EXPLORER_ARTIFACT_PATH = path.join(
  STAGE_ROOT,
  'question-explorer-results.json',
);
const REVIEWED_INPUT_PATH = path.join(RAW_ROOT, 'reviewed-input.json');
const CASE_AUTHOR_RECEIPT_PATH = path.join(
  RAW_ROOT,
  'case-author-actor-receipt.json',
);
const SYSTEM_OUTPUT_PATH = path.join(RAW_ROOT, 'system-output-reveal.json');
const TOOL_CONTRACT_PATH = path.join(
  ROOT,
  'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
);
const FROZEN_CASE_SOURCE_RELATIVE =
  'contracts/agent/country-outage-p1-page-coverage/s2/frozen-cases.json';
const FROZEN_CASE_SOURCE_PATH = path.join(ROOT, FROZEN_CASE_SOURCE_RELATIVE);

const SOURCE_EXECUTION_UNITS = {
  resolution: ['TOOL-01'],
  overview: ['TOOL-02'],
  series: ['TOOL-03'],
  asns: ['TOOL-04'],
  paths: ['TOOL-05'],
  audit: ['TOOL-06'],
  derived: ['OP-01', 'OP-02', 'OP-03'],
};

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function sha256Bytes(bytes) {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

function sha256File(filePath) {
  return sha256Bytes(fs.readFileSync(filePath));
}

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

function canonicalSha256(value) {
  // 与 Alignment Hook 的 json.dumps(..., sort_keys=True,
  // separators=(",", ":")) 保持相同的 ensure_ascii=True 语义。
  // 这使包含中文问题的案例集在 JS 生产器与 Python Hook 中得到同一摘要。
  const canonicalJson = JSON.stringify(canonicalize(value)).replace(
    /[^\x00-\x7f]/g,
    (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
  );
  return sha256Bytes(Buffer.from(canonicalJson));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function relative(filePath) {
  return path.relative(ROOT, filePath).split(path.sep).join('/');
}

function jsonPointer(value, pointer) {
  if (pointer === '') return value;
  if (!pointer.startsWith('/')) return undefined;
  return pointer
    .slice(1)
    .split('/')
    .reduce(
      (current, token) =>
        current?.[token.replaceAll('~1', '/').replaceAll('~0', '~')],
      value,
    );
}

function resolveSchema(schema, sourcePath, stack = new Set()) {
  if (Array.isArray(schema)) {
    return schema.map((item) => resolveSchema(item, sourcePath, new Set(stack)));
  }
  if (!schema || typeof schema !== 'object') return schema;
  if (typeof schema.$ref === 'string') {
    const [filePart, fragment = ''] = schema.$ref.split('#');
    const targetPath = filePart
      ? path.resolve(path.dirname(sourcePath), filePart)
      : sourcePath;
    const identity = `${targetPath}#${fragment}`;
    if (stack.has(identity)) return {};
    if (!fs.existsSync(targetPath)) {
      throw new Error(`Tool Contract $ref 文件不存在：${relative(targetPath)}`);
    }
    const targetRoot = readJson(targetPath);
    const target = jsonPointer(targetRoot, fragment);
    if (target === undefined) {
      throw new Error(`Tool Contract $ref 无法解析：${schema.$ref}`);
    }
    const nextStack = new Set(stack);
    nextStack.add(identity);
    const siblings = Object.fromEntries(
      Object.entries(schema)
        .filter(([key]) => key !== '$ref')
        .map(([key, value]) => [
          key,
          resolveSchema(value, sourcePath, new Set(stack)),
        ]),
    );
    return {
      ...resolveSchema(target, targetPath, nextStack),
      ...siblings,
    };
  }
  return Object.fromEntries(
    Object.entries(schema).map(([key, value]) => [
      key,
      resolveSchema(value, sourcePath, new Set(stack)),
    ]),
  );
}

function sourceOutputs(executionReceipts, source) {
  const units = SOURCE_EXECUTION_UNITS[source] ?? [];
  return executionReceipts
    .filter((receipt) => units.includes(receipt.execution_unit))
    .map((receipt) => receipt.output);
}

function sameArray(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function evidenceIdentityMatches(evidence, identity) {
  return (
    evidence.incident_id === identity.incident_id &&
    evidence.publication_id === identity.publication_id &&
    evidence.revision === identity.revision &&
    evidence.collector_id === identity.collector_id
  );
}

function commonIdentityMatches(actual, identity, includeEventType) {
  const fields = [
    'incident_id',
    'publication_id',
    'revision',
    'collector_id',
    'cohort_id',
    'window_start_utc',
    'window_end_utc',
    'data_through',
    'is_final_in_data_range',
    'lifecycle_state',
  ];
  if (includeEventType) fields.unshift('event_type');
  return fields.every(
    (field) => JSON.stringify(actual?.[field]) === JSON.stringify(identity[field]),
  );
}

function outputIdentityMatches(executionReceipt, identity) {
  const output = executionReceipt.output;
  if (executionReceipt.execution_unit === 'TOOL-01') {
    return commonIdentityMatches(output, identity, true);
  }
  if (executionReceipt.execution_unit.startsWith('TOOL-')) {
    return commonIdentityMatches(output, identity, false);
  }
  if (!commonIdentityMatches(output?.source_identity, identity, true)) {
    return false;
  }
  if (executionReceipt.execution_unit === 'OP-02') {
    return (
      commonIdentityMatches(output?.ipv4?.source_identity, identity, true) &&
      commonIdentityMatches(output?.ipv6?.source_identity, identity, true)
    );
  }
  if (executionReceipt.execution_unit === 'OP-03') {
    return (output?.ordered_fact_nodes ?? []).every((fact) =>
      commonIdentityMatches(fact?.source_identity, identity, true)
    );
  }
  return true;
}

function groundingIdentityMatchesCandidate(identity, candidate) {
  const publication = candidate.components.data_publication;
  return (
    commonIdentityMatches(identity, publication, true) &&
    identity.country_code === publication.country_code
  );
}

function dependencyClosure(nodeId, nodeById) {
  const result = new Set([nodeId]);
  const visit = (currentId) => {
    const current = nodeById.get(currentId);
    for (const parentId of current?.depends_on ?? []) {
      if (result.has(parentId)) continue;
      result.add(parentId);
      visit(parentId);
    }
  };
  visit(nodeId);
  return result;
}

function verifyEvidenceValue(evidence, executionReceipt) {
  if (evidence.source === 'derived') {
    return (
      evidence.value_state === undefined &&
      evidence.value === JSON.stringify(executionReceipt.output)
    );
  }
  const value = jsonPointer(executionReceipt.output, evidence.field_path);
  if (value === undefined) return false;
  const nonScalar = value !== null && typeof value === 'object';
  if (nonScalar) {
    return (
      evidence.value === null &&
      evidence.value_state === 'non_scalar_hashed' &&
      evidence.value_hash_algorithm === 'sha256-json-stringify-v1' &&
      typeof evidence.value_sha256 === 'string' &&
      sha256Bytes(Buffer.from(JSON.stringify(value))) === evidence.value_sha256
    );
  }
  return (
    evidence.value_state === 'scalar' &&
    JSON.stringify(evidence.value) === JSON.stringify(value)
  );
}

function verifyGoalScopedEvidence(
  evidence,
  goalId,
  executionReceipts,
  identity,
) {
  if (
    evidence.evidence_ref !== `${evidence.source}:${evidence.field_path}` ||
    !evidenceIdentityMatches(evidence, identity)
  ) {
    return false;
  }
  const sourceUnits = SOURCE_EXECUTION_UNITS[evidence.source] ?? [];
  return executionReceipts.some(
    (receipt) =>
      receipt.goal_id === goalId &&
      sourceUnits.includes(receipt.execution_unit) &&
      receipt.evidence_refs.includes(evidence.evidence_ref) &&
      verifyEvidenceValue(evidence, receipt),
  );
}

function loadAndVerifyCandidate() {
  const candidate = readJson(CANDIDATE_PATH);
  const errors = [];
  for (const [sourcePath, source] of Object.entries(
    candidate.components.sources,
  )) {
    const actual = sha256File(path.join(ROOT, sourcePath));
    if (actual !== source.sha256) {
      errors.push(`候选组件漂移：${sourcePath}`);
    }
  }
  const actualIdentity = canonicalSha256(candidate.components);
  if (actualIdentity !== candidate.candidate_identity_sha256) {
    errors.push('候选身份摘要与组件集合不一致');
  }
  if (errors.length > 0) {
    throw new Error(errors.join('\n'));
  }
  return candidate;
}

function loadAndVerifyExplorer(candidate) {
  const wrapper = readJson(WRAPPER_PATH);
  const toolContract = readJson(TOOL_CONTRACT_PATH);
  const outputSchemas = new Map(
    toolContract.execution_units.map((unit) => [
      unit.unit_id,
      resolveSchema(unit.output_schema, TOOL_CONTRACT_PATH),
    ]),
  );
  const errors = [];
  const receipts = [];
  let nodeCount = 0;
  let evidenceCount = 0;
  let evidenceReferenceCount = 0;
  let outputHashCount = 0;
  let outputContractCount = 0;
  let answerEvidenceReferenceCount = 0;
  let nonScalarEvidenceCount = 0;

  if (wrapper.candidate_id !== candidate.candidate_id) {
    errors.push('问题探针候选身份不一致');
  }
  const caseSourcePath = path.resolve(wrapper.case_source_ref);
  const candidateCaseSource =
    candidate.components.sources[FROZEN_CASE_SOURCE_RELATIVE];
  if (
    caseSourcePath !== FROZEN_CASE_SOURCE_PATH ||
    !candidateCaseSource ||
    !fs.existsSync(caseSourcePath) ||
    sha256File(caseSourcePath) !== wrapper.case_source_sha256 ||
    sha256File(caseSourcePath) !== candidateCaseSource.sha256
  ) {
    errors.push('问题探针冻结案例源未绑定候选组件清单');
  } else if (
    wrapper.case_set_hash_algorithm !==
      'sha256-json-stringify-parsed-input-preserving-key-order-v1' ||
    sha256Bytes(Buffer.from(JSON.stringify(readJson(caseSourcePath)))) !==
      wrapper.case_set_sha256
  ) {
    errors.push('问题探针解析案例集摘要不一致');
  }
  const frozenCases = fs.existsSync(caseSourcePath)
    ? readJson(caseSourcePath)
    : [];
  const frozenCaseIds = new Set(frozenCases.map((item) => item.case_id));
  const wrapperCaseIds = new Set(wrapper.cases.map((item) => item.case_id));
  if (
    frozenCaseIds.size !== frozenCases.length ||
    wrapperCaseIds.size !== wrapper.cases.length ||
    frozenCaseIds.size !== wrapperCaseIds.size ||
    [...frozenCaseIds].some((caseId) => !wrapperCaseIds.has(caseId))
  ) {
    errors.push('问题探针没有双向一一覆盖候选冻结案例集');
  }
  const expectedEventIdentity = {
    event_reference: candidate.components.data_publication.event_reference,
    publication_id: candidate.components.data_publication.publication_id,
    revision: candidate.components.data_publication.revision,
    collector_id: candidate.components.data_publication.collector_id,
  };
  for (const item of wrapper.cases) {
    const receiptPath = path.join(RECEIPT_ROOT, item.raw_agent_receipt_ref);
    const actualReceiptSha = sha256File(receiptPath);
    const receipt = readJson(receiptPath);
    if (actualReceiptSha !== item.raw_agent_receipt_sha256) {
      errors.push(`${item.case_id} 原始 Agent 回执摘要不一致`);
    }
    for (const [field, expected] of [
      ['candidate_id', wrapper.candidate_id],
      ['stage', 'S2'],
      ['run_id', wrapper.run_id],
      ['actor_id', wrapper.actor_id],
      ['case_id', item.case_id],
    ]) {
      if (receipt[field] !== expected) {
        errors.push(`${item.case_id}.${field} 未绑定同一探针运行`);
      }
    }
    const frozenCase = frozenCases.find(
      (candidateCase) => candidateCase.case_id === item.case_id,
    );
    const frozenCaseFields = [
      'case_id',
      'page_outcome_ids',
      'expression_type',
      'persona',
      'conversation_seed',
      'question',
      'review_status',
    ];
    if (
      !frozenCase ||
      frozenCaseFields.some(
        (field) =>
          JSON.stringify(item[field]) !== JSON.stringify(frozenCase[field]),
      ) ||
      JSON.stringify(item.event_identity) !==
        JSON.stringify(expectedEventIdentity) ||
      JSON.stringify(receipt.event_identity) !==
        JSON.stringify(expectedEventIdentity) ||
      receipt.original_question !== frozenCase.question
    ) {
      errors.push(`${item.case_id} 未绑定冻结问题和事件身份`);
    }
    if (
      !groundingIdentityMatchesCandidate(
        receipt.grounding_plan?.identity,
        candidate,
      ) ||
      receipt.grounding_plan?.identity?.publication_id !==
        receipt.event_identity?.publication_id ||
      receipt.grounding_plan?.identity?.revision !==
        receipt.event_identity?.revision ||
      receipt.grounding_plan?.identity?.collector_id !==
        receipt.event_identity?.collector_id
    ) {
      errors.push(`${item.case_id} Grounding identity 未锚定候选 publication`);
    }
    if (
      receipt.state_receipt?.commit !== 'none' ||
      JSON.stringify(receipt.state_receipt?.before) !== '{}' ||
      JSON.stringify(receipt.state_receipt?.after) !== '{}'
    ) {
      errors.push(`${item.case_id} 在 S2 非预期提交了状态`);
    }
    const nodes = receipt.grounding_plan?.nodes ?? [];
    const executionReceipts = receipt.tool_and_operator_receipts ?? [];
    if (nodes.length !== executionReceipts.length) {
      errors.push(`${item.case_id} Grounding 节点与执行回执数量不一致`);
    }
    const nodeIds = new Set(nodes.map((node) => node.node_id));
    const receiptNodeIds = new Set(
      executionReceipts.map((executionReceipt) => executionReceipt.node_id),
    );
    if (nodeIds.size !== nodes.length) {
      errors.push(`${item.case_id} Grounding 含重复 node_id`);
    }
    if (receiptNodeIds.size !== executionReceipts.length) {
      errors.push(`${item.case_id} 执行回执含重复 node_id`);
    }
    if (
      nodeIds.size !== receiptNodeIds.size ||
      [...nodeIds].some((nodeId) => !receiptNodeIds.has(nodeId))
    ) {
      errors.push(`${item.case_id} Grounding 节点与执行回执不是一一映射`);
    }
    const evidenceIds = new Set(
      (receipt.evidence ?? []).map((evidence) => evidence.evidence_ref),
    );
    const nodeById = new Map(nodes.map((node) => [node.node_id, node]));
    const executionByNodeId = new Map(
      executionReceipts.map((executionReceipt) => [
        executionReceipt.node_id,
        executionReceipt,
      ]),
    );
    const evidenceById = new Map(
      (receipt.evidence ?? []).map((evidence) => [
        evidence.evidence_ref,
        evidence,
      ]),
    );
    if (evidenceIds.size !== (receipt.evidence ?? []).length) {
      errors.push(`${item.case_id} 含重复 evidence_ref`);
    }
    for (const executionReceipt of executionReceipts) {
      const groundingNode = nodes.find(
        (node) => node.node_id === executionReceipt.node_id,
      );
      if (!groundingNode) {
        errors.push(`${item.case_id} 含未登记节点的执行回执`);
        continue;
      }
      if (
        executionReceipt.goal_id !== groundingNode.goal_id ||
        executionReceipt.execution_unit !== groundingNode.execution_unit ||
        !sameArray(executionReceipt.capability_ids, groundingNode.capability_ids) ||
        !sameArray(executionReceipt.input_node_ids, groundingNode.depends_on)
      ) {
        errors.push(
          `${item.case_id}.${executionReceipt.node_id} 回执身份与 Grounding 节点不一致`,
        );
      }
      if (
        groundingNode.depends_on.some(
          (dependencyId) =>
            nodeById.get(dependencyId)?.goal_id !== groundingNode.goal_id,
        )
      ) {
        errors.push(
          `${item.case_id}.${executionReceipt.node_id} 含跨 goal 依赖`,
        );
      }
      const outputSha = sha256Bytes(
        Buffer.from(JSON.stringify(executionReceipt.output)),
      );
      if (
        executionReceipt.output_hash_algorithm !==
          'sha256-json-stringify-v1' ||
        outputSha !== executionReceipt.output_sha256
      ) {
        errors.push(`${item.case_id}.${executionReceipt.node_id} 输出摘要不一致`);
      } else {
        outputHashCount += 1;
      }
      const outputSchema = outputSchemas.get(executionReceipt.execution_unit);
      if (!outputSchema) {
        errors.push(
          `${item.case_id}.${executionReceipt.node_id} 缺少登记输出合同`,
        );
      } else {
        const contractErrors = [...Errors(outputSchema, executionReceipt.output)];
        if (contractErrors.length > 0) {
          errors.push(
            `${item.case_id}.${executionReceipt.node_id} 输出不符合 ${executionReceipt.execution_unit} 合同：${contractErrors
              .slice(0, 3)
              .map((error) => `${error.instancePath || '/'} ${error.message}`)
              .join('；')}`,
          );
        } else {
          outputContractCount += 1;
        }
      }
      if (
        executionReceipt.status !== 'failed' &&
        !outputIdentityMatches(
          executionReceipt,
          receipt.grounding_plan.identity,
        )
      ) {
        errors.push(
          `${item.case_id}.${executionReceipt.node_id} 输出身份与 Grounding identity 不一致`,
        );
      }
      const allowedNodeIds = dependencyClosure(
        executionReceipt.node_id,
        nodeById,
      );
      const allowedReceipts = [...allowedNodeIds]
        .map((nodeId) => executionByNodeId.get(nodeId))
        .filter(Boolean);
      for (const evidenceRef of executionReceipt.evidence_refs ?? []) {
        evidenceReferenceCount += 1;
        const evidence = evidenceById.get(evidenceRef);
        if (!evidence) {
          errors.push(
            `${item.case_id}.${executionReceipt.node_id} 证据引用不存在：${evidenceRef}`,
          );
        } else if (
          !evidenceIdentityMatches(
            evidence,
            receipt.grounding_plan.identity,
          ) ||
          !allowedReceipts.some(
            (producer) =>
              (SOURCE_EXECUTION_UNITS[evidence.source] ?? []).includes(
                producer.execution_unit,
              ) && verifyEvidenceValue(evidence, producer),
          )
        ) {
          errors.push(
            `${item.case_id}.${executionReceipt.node_id} 证据不属于当前节点或其依赖闭包：${evidenceRef}`,
          );
        }
      }
    }
    for (const result of receipt.answer?.results ?? []) {
      const decision = (receipt.grounding_plan?.decisions ?? []).find(
        (item) => item.goal_id === result.goal_id,
      );
      const factBearing = ['supported', 'partial'].includes(
        result.answerability,
      );
      if (factBearing && (result.evidence_refs ?? []).length === 0) {
        errors.push(`${item.case_id}.${result.goal_id} 可发布事实没有证据`);
      }
      if (
        !factBearing &&
        ((result.evidence_refs ?? []).length > 0 ||
          (decision?.node_ids ?? []).length > 0)
      ) {
        errors.push(
          `${item.case_id}.${result.goal_id} 不可执行子目标含事实证据或执行节点`,
        );
      }
      for (const evidenceRef of result.evidence_refs ?? []) {
        answerEvidenceReferenceCount += 1;
        const evidence = evidenceById.get(evidenceRef);
        if (!evidence) {
          errors.push(`${item.case_id} 回答证据引用不存在：${evidenceRef}`);
        } else if (
          !verifyGoalScopedEvidence(
            evidence,
            result.goal_id,
            executionReceipts,
            receipt.grounding_plan.identity,
          )
        ) {
          errors.push(
            `${item.case_id}.${result.goal_id} 回答证据未绑定本目标的精确 Tool/算子输出：${evidenceRef}`,
          );
        }
      }
    }
    for (const evidence of receipt.evidence ?? []) {
      const producerReceipts = executionReceipts.filter(
        (executionReceipt) =>
          executionReceipt.evidence_refs.includes(evidence.evidence_ref) &&
          (SOURCE_EXECUTION_UNITS[evidence.source] ?? []).includes(
            executionReceipt.execution_unit,
          ) &&
          verifyEvidenceValue(evidence, executionReceipt),
      );
      const consumedByAnswer = (receipt.answer?.results ?? []).some((result) =>
          (result.evidence_refs ?? []).includes(evidence.evidence_ref)
        );
      const consumedByDownstream = executionReceipts.some(
        (consumer) =>
          consumer.evidence_refs.includes(evidence.evidence_ref) &&
          producerReceipts.every(
            (producer) => producer.node_id !== consumer.node_id,
          ),
      );
      if (
        producerReceipts.length === 0 ||
        (!consumedByAnswer && !consumedByDownstream)
      ) {
        errors.push(
          `${item.case_id} Evidence 没有闭合的 producer/consumer：${evidence.evidence_ref}`,
        );
      }
      if (evidence.value_state !== 'non_scalar_hashed') continue;
      nonScalarEvidenceCount += 1;
      const referencedByGoal = (receipt.answer?.results ?? []).some(
        (result) =>
          (result.evidence_refs ?? []).includes(evidence.evidence_ref) &&
          verifyGoalScopedEvidence(
            evidence,
            result.goal_id,
            executionReceipts,
            receipt.grounding_plan.identity,
          ),
      );
      if (!referencedByGoal) {
        errors.push(
          `${item.case_id} 非标量证据无法从本目标的精确 Tool 输出重算：${evidence.evidence_ref}`,
        );
      }
    }
    nodeCount += executionReceipts.length;
    evidenceCount += (receipt.evidence ?? []).length;
    receipts.push({
      case_id: item.case_id,
      path: relative(receiptPath),
      sha256: actualReceiptSha,
      execution_status: item.execution_status,
      goal_count: receipt.user_goal_plan?.goals?.length ?? 0,
      grounding_node_count: nodes.length,
      execution_receipt_count: executionReceipts.length,
      evidence_count: receipt.evidence?.length ?? 0,
      state_commit: receipt.state_receipt?.commit ?? null,
    });
  }
  if (errors.length > 0) {
    throw new Error(errors.join('\n'));
  }
  return {
    wrapper,
    receipts,
    mechanicalValidation: {
      case_receipt_sha256: `${receipts.length}/${receipts.length}`,
      grounding_execution_count: `${nodeCount}/${nodeCount}`,
      output_sha256: `${outputHashCount}/${nodeCount}`,
      output_contract_schema: `${outputContractCount}/${nodeCount}`,
      evidence_reference_resolution: `${evidenceReferenceCount}/${evidenceReferenceCount}`,
      answer_evidence_reference_resolution: `${answerEvidenceReferenceCount}/${answerEvidenceReferenceCount}`,
      non_scalar_evidence_hash: `${nonScalarEvidenceCount}/${nonScalarEvidenceCount}`,
      state_commit_none: `${receipts.length}/${receipts.length}`,
      total_evidence_items: evidenceCount,
      result: 'PASS',
    },
  };
}

function buildExplorerCases(wrapper) {
  return wrapper.cases.map((item) => ({
    ...item,
    raw_agent_receipt_ref: relative(
      path.join(RECEIPT_ROOT, item.raw_agent_receipt_ref),
    ),
  }));
}

function prepare() {
  const candidate = loadAndVerifyCandidate();
  const { wrapper, receipts, mechanicalValidation } =
    loadAndVerifyExplorer(candidate);
  const cases = buildExplorerCases(wrapper);
  const casesSha256 = canonicalSha256(cases);

  const rawAggregate = {
    schema_version: 'country_outage_p1_page_coverage_s2_raw_agent_receipts_v1',
    evidence_kind: 'raw_agent_receipts',
    candidate_id: candidate.candidate_id,
    candidate_identity_sha256: candidate.candidate_identity_sha256,
    stage: 'S2',
    run_id: wrapper.run_id,
    actor_id: wrapper.actor_id,
    captured_at: wrapper.captured_at,
    question_explorer_actor_id: wrapper.question_explorer_actor_id,
    question_explorer_run_id: wrapper.question_explorer_run_id,
    page_outcome_ids: wrapper.page_outcome_ids,
    cases_sha256: casesSha256,
    cases_hash_algorithm: 'sha256-recursive-sorted-key-canonical-json-v1',
    source_wrapper: {
      path: relative(WRAPPER_PATH),
      sha256: sha256File(WRAPPER_PATH),
    },
    source_case_set: {
      path: relative(path.resolve(wrapper.case_source_ref)),
      sha256: wrapper.case_source_sha256,
      parsed_case_set_sha256: wrapper.case_set_sha256,
      parsed_case_set_hash_algorithm: wrapper.case_set_hash_algorithm,
    },
    receipts,
    mechanical_validation: mechanicalValidation,
  };
  writeJson(RAW_AGGREGATE_PATH, rawAggregate);
  const rawAggregateSha = sha256File(RAW_AGGREGATE_PATH);

  const explorerArtifact = {
    schema_version: 'country_outage_p1_page_coverage_s2_question_explorer_results_v1',
    artifact_kind: 'question_explorer_results',
    stage: 'S2',
    candidate_id: candidate.candidate_id,
    status: 'PASS',
    captured_at: new Date().toISOString(),
    question_explorer_actor_id: wrapper.actor_id,
    question_explorer_run_id: wrapper.run_id,
    page_outcome_ids: wrapper.page_outcome_ids,
    cases,
    cases_sha256: casesSha256,
    raw_agent_receipts_sha256: rawAggregateSha,
    mechanical_validation: mechanicalValidation,
    evidence_refs: [
      {
        kind: 'raw_agent_receipts',
        path: relative(RAW_AGGREGATE_PATH),
        sha256: rawAggregateSha,
      },
      {
        kind: 'candidate_identity',
        path: relative(CANDIDATE_PATH),
        sha256: sha256File(CANDIDATE_PATH),
      },
    ],
  };
  writeJson(EXPLORER_ARTIFACT_PATH, explorerArtifact);

  const preparedAt = new Date().toISOString();
  const reviewedInput = {
    schema_version: 'country_outage_p1_page_coverage_s2_reviewed_input_v1',
    evidence_kind: 'reviewed_input',
    candidate_id: candidate.candidate_id,
    candidate_identity_sha256: candidate.candidate_identity_sha256,
    stage: 'S2',
    run_id: wrapper.run_id,
    actor_id: wrapper.actor_id,
    captured_at: preparedAt,
    question_explorer_receipt_sha256: rawAggregateSha,
    question_explorer_cases_sha256: casesSha256,
    questions: cases.map((item) => ({
      case_id: item.case_id,
      page_outcome_ids: item.page_outcome_ids,
      expression_type: item.expression_type,
      persona: item.persona,
      conversation_seed: item.conversation_seed,
      question: item.question,
      review_status: item.review_status,
      event_identity: item.event_identity,
    })),
    truth_sources: [
      'docs/agent/P1-聊天问答/Task-Spec-最终验收文档.md',
      'docs/agent/P1-聊天问答/Plan-分阶段计划.md',
      'evaluation/country-outage/p1-page-coverage/s0/page-capability-outcome-map.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/oracle.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/policy.json',
    ],
    unrevealed_system_outputs: [
      'evaluation/country-outage/p1-page-coverage/s2/raw/agent-receipts/**',
      'evaluation/country-outage/p1-page-coverage/s2/raw/raw-agent-receipts.json',
    ],
    denied_actions: [
      'write_system_output',
      'mark_pass',
      'modify_implementation',
      'modify_test',
      'modify_contract',
      'modify_oracle',
    ],
  };
  writeJson(REVIEWED_INPUT_PATH, reviewedInput);

  const caseAuthorReceipt = {
    schema_version: 'country_outage_p1_page_coverage_s2_actor_receipt_v1',
    evidence_kind: 'case_author_actor_receipt',
    candidate_id: candidate.candidate_id,
    stage: 'S2',
    run_id: wrapper.run_id,
    actor_id: wrapper.actor_id,
    captured_at: preparedAt,
    orchestrator_receipt_id: 's2-question-explorer-orchestrator-receipt-001',
    allowed_actions: [
      'generate_probe_cases',
      'execute_frozen_questions',
      'capture_raw_receipts',
    ],
    denied_actions: [
      'write_truth',
      'mark_pass',
      'modify_implementation',
      'modify_contract',
      'modify_oracle',
    ],
    reviewed_input_sha256: sha256File(REVIEWED_INPUT_PATH),
  };
  writeJson(CASE_AUTHOR_RECEIPT_PATH, caseAuthorReceipt);

  process.stdout.write(
    `${JSON.stringify(
      {
        candidate_id: candidate.candidate_id,
        case_count: cases.length,
        cases_sha256: casesSha256,
        raw_agent_receipts_sha256: rawAggregateSha,
        reviewed_input_sha256: sha256File(REVIEWED_INPUT_PATH),
        case_author_actor_receipt_sha256: sha256File(
          CASE_AUTHOR_RECEIPT_PATH,
        ),
        mechanical_validation: mechanicalValidation,
      },
      null,
      2,
    )}\n`,
  );
}

function reveal() {
  const candidate = loadAndVerifyCandidate();
  const { wrapper } = loadAndVerifyExplorer(candidate);
  const explorerArtifact = readJson(EXPLORER_ARTIFACT_PATH);
  const reviewedInputSha = sha256File(REVIEWED_INPUT_PATH);
  const capturedAt = new Date().toISOString();
  const outputs = wrapper.cases.map((item) => {
    const receiptPath = path.join(RECEIPT_ROOT, item.raw_agent_receipt_ref);
    const receipt = readJson(receiptPath);
    return {
      case_id: item.case_id,
      original_question: receipt.original_question,
      raw_agent_receipt_ref: relative(receiptPath),
      raw_agent_receipt_sha256: sha256File(receiptPath),
      user_goal_plan: receipt.user_goal_plan,
      grounding_plan: receipt.grounding_plan,
      tool_and_operator_receipts: receipt.tool_and_operator_receipts,
      evidence: receipt.evidence,
      answer: receipt.answer,
      state_receipt: receipt.state_receipt,
      error: receipt.error,
    };
  });
  const systemOutput = {
    schema_version: 'country_outage_p1_page_coverage_s2_system_output_v1',
    evidence_kind: 'system_output',
    candidate_id: candidate.candidate_id,
    candidate_identity_sha256: candidate.candidate_identity_sha256,
    stage: 'S2',
    run_id: 's2-final-system-output-run-001',
    actor_id: 'p1-page-capability-runtime',
    captured_at: capturedAt,
    reviewed_input_sha256: reviewedInputSha,
    question_explorer_receipt_sha256:
      explorerArtifact.raw_agent_receipts_sha256,
    question_explorer_cases_sha256: explorerArtifact.cases_sha256,
    raw_agent_receipts: {
      path: relative(RAW_AGGREGATE_PATH),
      sha256: sha256File(RAW_AGGREGATE_PATH),
    },
    outputs,
  };
  writeJson(SYSTEM_OUTPUT_PATH, systemOutput);
  process.stdout.write(
    `${JSON.stringify(
      {
        candidate_id: candidate.candidate_id,
        captured_at: capturedAt,
        reviewed_input_sha256: reviewedInputSha,
        system_output_sha256: sha256File(SYSTEM_OUTPUT_PATH),
        output_count: outputs.length,
      },
      null,
      2,
    )}\n`,
  );
}

const command = process.argv[2];
if (command === 'prepare') {
  prepare();
} else if (command === 'reveal') {
  reveal();
} else {
  throw new Error(
    '用法：capture_country_outage_p1_page_coverage_s2_review.mjs prepare|reveal',
  );
}
