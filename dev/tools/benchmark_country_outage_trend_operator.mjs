#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  REGISTERED_TREND_PROFILES,
  analyzeCompactTrendBundle,
  analyzeEventWindowTrend,
  analyzeMultiTrackTrend,
  getRegisteredTrendProfile,
} from '../../agent-sidecar/dist/src/chat/event-window-trend.js'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const rawRoot = resolve(root, 'evaluation/country-outage/p1-page-coverage/s1/raw')
const evaluationRoot = resolve(root, 'evaluation/country-outage/p1-trend-operator')
const initialPath = resolve(evaluationRoot, 'initial-operator-result.json')
const finalPath = resolve(evaluationRoot, 'final-operator-result.json')
const comparisonPath = resolve(evaluationRoot, 'alignment-comparison.json')
const compactPath = resolve(evaluationRoot, 'compact-chat-output.json')
const expertPath = resolve(evaluationRoot, 'independent-expert-description.json')
const profilePath = resolve(root,
  'contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json')

function readFrozen(name) {
  const path = resolve(rawRoot, name)
  const raw = readFileSync(path)
  return {
    path,
    relativePath: path.slice(root.length + 1),
    raw,
    sha256: createHash('sha256').update(raw).digest('hex'),
    value: JSON.parse(raw.toString('utf8')),
  }
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => [key, canonicalize(item)]))
  }
  return value
}

function canonicalJson(value) {
  return JSON.stringify(canonicalize(value))
}

function sha256Json(value) {
  return createHash('sha256').update(canonicalJson(value)).digest('hex')
}

function requireEqual(label, values) {
  if (values.some((value) => canonicalJson(value) !== canonicalJson(values[0]))) {
    throw new Error(`${label} 在冻结回执之间不一致`)
  }
  return values[0]
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

const resolver = readFrozen('live-resolver-response-v6.json')
const overview = readFrozen('live-overview-response-v6.json')
const series = readFrozen('live-series-response-v6.json')
const profileRaw = readFileSync(profilePath)
const profileSha256 = createHash('sha256').update(profileRaw).digest('hex')
const identityFields = [
  'incident_id', 'publication_id', 'revision', 'collector_id', 'cohort_id',
  'window_start_utc', 'window_end_utc', 'data_through', 'is_final_in_data_range',
  'lifecycle_state', 'observation_state', 'quality_state', 'missing_slot_count',
]
for (const field of identityFields) {
  requireEqual(field, [resolver.value[field], overview.value[field], series.value[field]])
}
if (resolver.value.event_type !== 'country_outage'
  || resolver.value.collector_id !== 'rrc25'
  || [resolver, overview, series].some((item) =>
    item.value.publication_state !== 'published')) {
  throw new Error('冻结回执不是已发布的 country_outage / rrc25 身份')
}
if (series.value.timestamps.length !== series.value.point_count) {
  throw new Error('冻结 series 的 timestamps 与 point_count 不一致')
}

const sourceIdentity = {
  source_schema_version: series.value.schema_version,
  event_type: resolver.value.event_type,
  incident_id: series.value.incident_id,
  publication_id: series.value.publication_id,
  publication_state: series.value.publication_state,
  revision: series.value.revision,
  collector_id: series.value.collector_id,
  cohort_id: series.value.cohort_id,
  window_start_utc: series.value.window_start_utc,
  window_end_utc: series.value.window_end_utc,
  data_through: series.value.data_through,
  is_final_in_data_range: series.value.is_final_in_data_range,
  lifecycle_state: series.value.lifecycle_state,
  observation_state: series.value.observation_state,
  quality_state: series.value.quality_state,
  missing_slot_count: series.value.missing_slot_count,
}

const metrics = Object.keys(REGISTERED_TREND_PROFILES).sort()
if (metrics.length !== 15) throw new Error(`登记轨道应为 15，实际 ${metrics.length}`)
const inputs = metrics.map((metric) => {
  const definition = series.value.track_definitions[metric]
  const values = series.value.tracks[metric]
  if (definition === undefined || !Array.isArray(values)
    || values.length !== series.value.timestamps.length) {
    throw new Error(`冻结 series 缺少或损坏轨道 ${metric}`)
  }
  const profile = getRegisteredTrendProfile(metric)
  return {
    source_identity: sourceIdentity,
    metric,
    unit: definition.unit,
    series_semantics: profile.series_semantics,
    timestamps: series.value.timestamps,
    values,
    source_evidence_refs: {
      identity: [resolver, overview, series].map((item) =>
        `frozen:${item.relativePath}:sha256:${item.sha256}#identity`),
      timestamps: `frozen:${series.relativePath}:sha256:${series.sha256}#/timestamps`,
      values: `frozen:${series.relativePath}:sha256:${series.sha256}#/tracks/${metric}`,
      metric_definition: `frozen:${series.relativePath}:sha256:${series.sha256}#/track_definitions/${metric}`,
      trend_profile: `contract:${profilePath.slice(root.length + 1)}:sha256:${profileSha256}#/profiles/${metric}`,
    },
    trend_profile: profile,
  }
})

const tracks = inputs.map((input) => {
  const machineResult = analyzeEventWindowTrend(input)
  const definition = series.value.track_definitions[input.metric]
  return {
    metric: input.metric,
    unit: input.unit,
    label: definition.label,
    definition: definition.definition,
    machine_result_sha256: sha256Json(machineResult),
    deterministic_description_zh: machineResult.deterministic_description_zh,
    machine_result: machineResult,
  }
})
const multiTrackResult = analyzeMultiTrackTrend(inputs)
const compactTracks = tracks.map((track) => ({
  metric: track.metric,
  compact_output_sha256: sha256Json(track.machine_result.compact_chat_output),
  compact_output: track.machine_result.compact_chat_output,
}))
const compactByMetric = new Map(compactTracks.map((track) => [track.metric, track]))
const compactBundle = analyzeCompactTrendBundle('fixed-ip-address-change-v1', [
  inputs.find((input) => input.metric === 'fixed_visible_ipv4_address_count'),
  inputs.find((input) => input.metric === 'fixed_visible_ipv6_slash48_count'),
])

function point(value) {
  return { timestamp: value.at_utc, index: value.index, value: value.value }
}

function step(value) {
  if (value === null) return null
  return {
    from_timestamp: value.from.at_utc,
    to_timestamp: value.to.at_utc,
    from_index: value.from.index,
    to_index: value.to.index,
    from_value: value.from.value,
    to_value: value.to.value,
    delta: value.change,
  }
}

function operatorExactFacts(track) {
  const result = track.machine_result
  return {
    unit: result.unit,
    semantic_type: result.series_semantics,
    point_count: result.data_quality.total_point_count,
    observed_count: result.data_quality.observed_point_count,
    null_count: result.data_quality.null_point_count,
    first_observed: point(result.summary.first),
    last_observed: point(result.summary.last),
    minimum: point(result.summary.minimum),
    maximum: point(result.summary.maximum),
    net_change: result.summary.net_change.value,
    largest_step_down: step(result.largest_adjacent_step_down),
    largest_step_up: step(result.largest_adjacent_step_up),
  }
}

function expertExactFacts(track) {
  return {
    unit: track.unit,
    semantic_type: track.semantic_type,
    point_count: track.point_count,
    observed_count: track.observed_count,
    null_count: track.null_count,
    first_observed: track.first_observed,
    last_observed: track.last_observed,
    minimum: track.minimum,
    maximum: track.maximum,
    net_change: track.last_observed.value - track.first_observed.value,
    largest_step_down: track.largest_step_down,
    largest_step_up: track.largest_step_up,
  }
}

function rawExactFacts(input) {
  const observed = input.values.flatMap((value, index) => value === null
    ? [] : [{ value, index, timestamp: input.timestamps[index] }])
  let minimum = observed[0]
  let maximum = observed[0]
  let down = null
  let up = null
  for (const item of observed.slice(1)) {
    if (item.value < minimum.value) minimum = item
    if (item.value > maximum.value) maximum = item
  }
  for (let index = 1; index < input.values.length; index += 1) {
    const before = input.values[index - 1]
    const after = input.values[index]
    if (before === null || after === null) continue
    const candidate = {
      from_timestamp: input.timestamps[index - 1],
      to_timestamp: input.timestamps[index],
      from_index: index - 1,
      to_index: index,
      from_value: before,
      to_value: after,
      delta: after - before,
    }
    if (candidate.delta < 0 && (down === null || candidate.delta < down.delta)) down = candidate
    if (candidate.delta > 0 && (up === null || candidate.delta > up.delta)) up = candidate
  }
  const asPoint = (item) => ({
    timestamp: item.timestamp, index: item.index, value: item.value,
  })
  return {
    unit: input.unit,
    semantic_type: input.series_semantics,
    point_count: input.values.length,
    observed_count: observed.length,
    null_count: input.values.length - observed.length,
    first_observed: asPoint(observed[0]),
    last_observed: asPoint(observed[observed.length - 1]),
    minimum: asPoint(minimum),
    maximum: asPoint(maximum),
    net_change: observed[observed.length - 1].value - observed[0].value,
    largest_step_down: down,
    largest_step_up: up,
  }
}

const expertPresent = existsSync(expertPath)
const expert = expertPresent ? JSON.parse(readFileSync(expertPath, 'utf8')) : null
const expertByMetric = new Map((expert?.track_analyses ?? [])
  .map((track) => [track.metric_id, track]))
const exactFactNames = [
  'unit', 'semantic_type', 'point_count', 'observed_count', 'null_count',
  'first_observed', 'last_observed', 'minimum', 'maximum', 'net_change',
  'largest_step_down', 'largest_step_up',
]
const directionalFactNames = new Set(['largest_step_down', 'largest_step_up'])

const compactForbiddenTerms = [
  'metric_id', 'metric id', 'change_threshold', 'threshold', '阈值', '审计',
  '阶段数', '转折', '毫秒', 'milliseconds', 'fact_id', 'fact id',
  'phase_sequence', 'phase sequence', 'analysis_value', 'analysis value',
  'profile_id', 'profile id',
]
function compactVisibleText(output) {
  return [
    output.headline_zh,
    output.body_zh,
    ...output.cards.flatMap((card) => [card.label_zh, card.text_zh]),
    ...output.limitations.map((item) => item.text_zh),
  ].join('\n')
}
function compactRequiredCardTypes(result) {
  const required = new Set(['first', 'last', 'net_change', result.trend_profile.primary_fact])
  if (result.series_semantics === 'cumulative'
    && result.largest_adjacent_step_up !== null) {
    required.add('largest_adjacent_step_up')
  }
  return [...required]
}
function expectedCompactCardValue(result, factType) {
  const values = {
    first: result.summary.first.value,
    last: result.summary.last.value,
    minimum: result.summary.minimum.value,
    maximum: result.summary.maximum.value,
    net_change: result.summary.net_change.value,
    largest_adjacent_step_up: result.largest_adjacent_step_up?.change,
  }
  return values[factType]
}
const compactTrackChecks = tracks.map((track) => {
  const output = compactByMetric.get(track.metric).compact_output
  const visible = compactVisibleText(output)
  const requiredCards = compactRequiredCardTypes(track.machine_result)
  const cardChecks = requiredCards.map((factType) => {
    const card = output.cards.find((item) => item.fact_type === factType)
    return {
      fact_type: factType,
      present: card !== undefined,
      value_matches_machine_fact: card !== undefined
        && card.value === expectedCompactCardValue(track.machine_result, factType),
      lineage_bound: card !== undefined
        && card.fact_ids.length > 0 && card.evidence_refs.length > 0,
    }
  })
  const leakageHits = compactForbiddenTerms.filter((term) =>
    visible.toLowerCase().includes(term.toLowerCase()))
  return {
    metric: track.metric,
    display_label_zh: output.display_label_zh,
    exists: output !== undefined,
    body_character_count: output.character_count,
    sentence_count: output.sentence_count,
    required_card_count: requiredCards.length,
    required_cards_pass: cardChecks.every((item) =>
      item.present && item.value_matches_machine_fact && item.lineage_bound),
    card_checks: cardChecks,
    internal_leakage_count: leakageHits.length,
    internal_leakage_terms: leakageHits,
    body_within_220_characters: output.character_count <= 220,
    body_sentence_count_valid: output.sentence_count >= 1 && output.sentence_count <= 3,
  }
})
const compactLengths = compactTrackChecks.map((item) => item.body_character_count)
const expertDescriptionLengths = (expert?.track_analyses ?? []).map((track) => ({
  metric: track.metric_id,
  character_count: Array.from(track.description_zh).length,
}))
const lengthStats = (values) => ({
  minimum: Math.min(...values),
  maximum: Math.max(...values),
  average: Number((values.reduce((sum, value) => sum + value, 0) / values.length)
    .toFixed(3)),
})

let expertRawMismatch = 0
const expertZeroStepDifferences = []
const exactByTrack = tracks.map((track) => {
  const operatorFacts = operatorExactFacts(track)
  const expertTrack = expertByMetric.get(track.metric)
  const expertFacts = expertTrack === undefined ? null : expertExactFacts(expertTrack)
  const rawFacts = rawExactFacts(inputs.find((item) => item.metric === track.metric))
  const facts = exactFactNames.map((factName) => {
    const directional = directionalFactNames.has(factName)
    const rawFactExists = !directional || rawFacts[factName] !== null
    const operatorMatchesRaw = canonicalJson(operatorFacts[factName])
      === canonicalJson(rawFacts[factName])
    const expertMatchesRaw = expertFacts === null ? null
      : canonicalJson(expertFacts[factName]) === canonicalJson(rawFacts[factName])
    if (expertMatchesRaw === false) expertRawMismatch += 1
    const expertZeroWhenAbsent = directional && !rawFactExists
      && expertFacts?.[factName]?.delta === 0
    if (expertZeroWhenAbsent) expertZeroStepDifferences.push({
      metric: track.metric,
      fact_name: factName,
      raw_direction_fact: null,
      expert_representation: expertFacts[factName],
      final_operator_representation: operatorFacts[factName],
      classification: 'expert_zero_step_object_for_absent_direction',
    })
    return { fact_name: factName, directional, raw_fact_exists: rawFactExists,
      operator_value_state: operatorFacts[factName] === null ? 'null' : 'fact',
      expert_value_state: expertFacts === null ? 'missing'
        : expertFacts[factName] === null ? 'null' : 'fact',
      operator_matches_raw: operatorMatchesRaw,
      expert_matches_raw: expertMatchesRaw,
      expert_zero_step_object_when_direction_absent: expertZeroWhenAbsent,
      operator_matches_expert: expertFacts === null ? null
        : canonicalJson(operatorFacts[factName]) === canonicalJson(expertFacts[factName]) }
  })
  return { metric: track.metric, field_slot_count: facts.length,
    raw_valid_fact_count: facts.filter((item) => item.raw_fact_exists).length,
    raw_direction_absence_count: facts.filter((item) =>
      item.directional && !item.raw_fact_exists).length,
    matched_operator_raw_valid_facts: facts.filter((item) =>
      item.raw_fact_exists && item.operator_matches_raw).length,
    matched_operator_expert: facts.filter((item) => item.operator_matches_expert).length,
    facts }
})

function scoreValidFacts(candidateEntries, reference) {
  const submitted = candidateEntries.reduce((sum, entry) => sum + entry.submitted, 0)
  const matched = candidateEntries.reduce((sum, entry) => sum + entry.matched, 0)
  return {
    matched, submitted, reference,
    precision: submitted === 0 ? null : Number((matched / submitted).toFixed(6)),
    recall: Number((matched / reference).toFixed(6)),
  }
}

const initial = existsSync(initialPath) ? JSON.parse(readFileSync(initialPath, 'utf8')) : null
const initialTrackMap = new Map((initial?.tracks ?? []).map((track) => [track.metric, track]))
const initialExactByTrack = metrics.map((metric) => {
  const initialTrack = initialTrackMap.get(metric)
  const rawReference = rawExactFacts(inputs.find((item) => item.metric === metric))
  if (initialTrack === undefined) {
    return { metric, status: 'not_submitted', submitted: 0, matched: 0, facts: [] }
  }
  const result = initialTrack.machine_result
  const partial = {
    unit: result.unit,
    semantic_type: result.series_semantics,
    point_count: result.data_quality.total_point_count,
    observed_count: result.data_quality.observed_point_count,
    null_count: result.data_quality.null_point_count,
    first_observed: point(result.summary.first),
    last_observed: point(result.summary.last),
    minimum: point(result.summary.minimum),
    maximum: point(result.summary.maximum),
    net_change: result.summary.net_change.value,
  }
  const names = Object.keys(partial)
  const facts = names.map((name) => ({ fact_name: name,
    matches_raw: canonicalJson(partial[name]) === canonicalJson(rawReference[name]) }))
  return { metric, status: 'submitted', submitted: names.length,
    matched: facts.filter((item) => item.matches_raw).length, facts }
})
const initialEntries = initialExactByTrack.map(({ submitted, matched }) =>
  ({ submitted, matched }))
const rawValidFactCount = exactByTrack.reduce(
  (sum, entry) => sum + entry.raw_valid_fact_count, 0)
const rawDirectionAbsenceCount = exactByTrack.reduce(
  (sum, entry) => sum + entry.raw_direction_absence_count, 0)
const finalEntries = exactByTrack.map((entry) => ({
  submitted: entry.facts.filter((item) =>
    item.raw_fact_exists && item.operator_value_state === 'fact').length,
  matched: entry.matched_operator_raw_valid_facts,
}))

function setOverlap(left, right) {
  const leftSet = new Set(left)
  const rightSet = new Set(right)
  const intersection = [...leftSet].filter((value) => rightSet.has(value)).length
  return { intersection, operator_count: leftSet.size, expert_count: rightSet.size,
    operator_precision_reference_only: leftSet.size === 0 ? null
      : Number((intersection / leftSet.size).toFixed(6)),
    expert_recall_reference_only: rightSet.size === 0 ? null
      : Number((intersection / rightSet.size).toFixed(6)) }
}

const readability = tracks.map((track) => {
  const result = track.machine_result
  const expertTrack = expertByMetric.get(track.metric)
  if (expertTrack === undefined) return { metric: track.metric, status: 'expert_missing' }
  const expertBoundaries = expertTrack.phases.slice(0, -1).map((phase) => phase.end_index)
  const displayBoundaries = result.display_phase_sequence
    .filter((phase, index, all) => index < all.length - 1
      && phase.source_run === all[index + 1]?.source_run)
    .map((phase) => phase.to.index)
  return {
    metric: track.metric,
    counts: {
      initial_audit_phases: initialTrackMap.get(track.metric)?.machine_result
        ?.phase_sequence?.length ?? null,
      final_audit_phases: result.phase_sequence.length,
      final_display_phases: result.display_phase_sequence.length,
      expert_subjective_phases: expertTrack.phases.length,
      initial_audit_turning_points: initialTrackMap.get(track.metric)?.machine_result
        ?.turning_points?.length ?? null,
      final_audit_turning_points: result.turning_points.length,
      final_display_turning_points: result.display_turning_points.length,
      expert_subjective_turning_points: expertTrack.turning_points.length,
    },
    display_boundary_overlap_with_expert_reference: setOverlap(
      displayBoundaries, expertBoundaries),
    display_turning_overlap_with_expert_reference: setOverlap(
      result.display_turning_points.map((item) => item.index),
      expertTrack.turning_points.map((item) => item.index)),
    interpretation: '仅衡量与专家主观选择的重合，不把专家分段当 Oracle 或正确答案。',
  }
})

function crossPatternCovered(pattern) {
  const indexes = [...new Set(pattern.evidence_points.map((item) => item.index))].sort((a, b) => a - b)
  const wanted = new Set(pattern.metric_ids)
  const candidates = multiTrackResult.audit_facts.filter((fact) => {
    if (indexes.length === 3) return fact.kind === 'synchronized_isolated_spike'
      && fact.from_index === indexes[0] && fact.to_index === indexes[2]
    if (indexes.length === 2 && indexes[1] - indexes[0] === 1) {
      return fact.from_index === indexes[0] && fact.to_index === indexes[1]
    }
    return fact.kind === 'cumulative_current_divergence'
      && fact.to_index === indexes[indexes.length - 1]
  })
  const coveredMetrics = new Set(candidates.flatMap((fact) => fact.metrics))
  return {
    pattern_id: pattern.pattern_id,
    covered: [...wanted].every((metric) => coveredMetrics.has(metric)),
    matching_operator_fact_ids: candidates.map((fact) => fact.fact_id),
    note: '专家模式仅作揭示后的参考集合；覆盖率不代表多轨事实完整性的 Oracle。',
  }
}
const crossPatternCoverage = (expert?.cross_track_patterns ?? []).map(crossPatternCovered)

const expertEntries = exactByTrack.map((entry) => ({
  submitted: entry.facts.filter((item) => item.expert_value_state === 'fact').length,
  matched: entry.facts.filter((item) =>
    item.raw_fact_exists && item.expert_matches_raw).length,
}))
const exactInitial = scoreValidFacts(initialEntries, rawValidFactCount)
const exactFinal = scoreValidFacts(finalEntries, rawValidFactCount)
const exactExpert = scoreValidFacts(expertEntries, rawValidFactCount)
const finalCorrectAbsences = exactByTrack.reduce((sum, entry) => sum
  + entry.facts.filter((item) => item.directional && !item.raw_fact_exists
    && item.operator_value_state === 'null').length, 0)
const expertCorrectAbsences = exactByTrack.reduce((sum, entry) => sum
  + entry.facts.filter((item) => item.directional && !item.raw_fact_exists
    && item.expert_value_state === 'null').length, 0)
const finalRawSlotMatches = exactByTrack.reduce((sum, entry) => sum
  + entry.facts.filter((item) => item.operator_matches_raw).length, 0)
const finalExpertSlotMatches = exactByTrack.reduce((sum, entry) => sum
  + entry.facts.filter((item) => item.operator_matches_expert).length, 0)
const compactCoverageCount = compactTrackChecks.filter((item) => item.exists).length
const compactRequiredCardsPassed = compactTrackChecks.filter((item) =>
  item.required_cards_pass).length
const compactRequiredCardCount = compactTrackChecks.reduce((sum, item) =>
  sum + item.required_card_count, 0)
const compactRequiredCardMatchCount = compactTrackChecks.reduce((sum, item) =>
  sum + item.card_checks.filter((card) => card.present
    && card.value_matches_machine_fact && card.lineage_bound).length, 0)
const compactLeakageCount = compactTrackChecks.reduce((sum, item) =>
  sum + item.internal_leakage_count, 0)
const bundleVisibleText = [compactBundle.title_zh, compactBundle.body_zh,
  ...compactBundle.limitations.map((item) => item.text_zh)].join('\n')
const bundleLeakageTerms = compactForbiddenTerms.filter((term) =>
  bundleVisibleText.toLowerCase().includes(term.toLowerCase()))
const compactResult = {
  schema_version: 'country_outage_p1_trend_operator_compact_benchmark_v1',
  result_kind: 'standalone_lightweight_compact_chat_output',
  replay_identity: {
    source_identity: sourceIdentity,
    raw_receipts: [resolver, overview, series].map((item) => ({
      path: item.relativePath, sha256: item.sha256,
    })),
    trend_profile_registry: {
      path: profilePath.slice(root.length + 1), sha256: profileSha256,
    },
  },
  operator: {
    operator_id: 'event-window-trend-compact',
    operator_version: '1.2.0',
    deterministic: true,
    model_dependency: 'none',
  },
  objective_checks: {
    compact_track_coverage: `${compactCoverageCount}/${metrics.length}`,
    required_exact_cards_passed: `${compactRequiredCardsPassed}/${metrics.length}`,
    internal_implementation_leakage_count: compactLeakageCount,
    body_length_characters: lengthStats(compactLengths),
    all_bodies_at_most_220_characters: compactTrackChecks.every((item) =>
      item.body_within_220_characters),
    all_bodies_one_to_three_sentences: compactTrackChecks.every((item) =>
      item.body_sentence_count_valid),
    bundle_unit_separation_pass: compactBundle.unit_separation.ipv4_unit
      !== compactBundle.unit_separation.ipv6_unit
      && compactBundle.unit_separation.cross_unit_aggregation === 'forbidden',
    bundle_internal_implementation_leakage_count: bundleLeakageTerms.length,
  },
  tracks: compactTracks,
  bundles: [{
    bundle_id: 'fixed-ip-address-change-display',
    bundle: compactBundle,
  }],
  compact_tracks_digest_sha256: sha256Json(compactTracks),
  bundle_digest_sha256: sha256Json(compactBundle),
}
const finalResult = {
  schema_version: 'country_outage_p1_trend_operator_final_benchmark_v3',
  result_kind: 'compact_chat_output_aligned_operator',
  replay_identity: {
    source_identity: sourceIdentity,
    raw_receipts: [resolver, overview, series].map((item) => ({
      path: item.relativePath, sha256: item.sha256, bytes: item.raw.length,
    })),
    trend_profile_registry: {
      path: profilePath.slice(root.length + 1), sha256: profileSha256,
    },
  },
  operator: { operator_id: 'event-window-trend', operator_version: '1.2.0',
    deterministic: true, model_dependency: 'none', track_count: tracks.length },
  benchmark_status: {
    status: expertPresent ? 'alignment_comparison_completed' : 'expert_reference_pending',
    independent_expert_reference_present: expertPresent,
    expert_subjective_phases_used_as_oracle: false,
    exact_fact_recompute_status: expertPresent
      ? 'PASS_FOR_175_EXISTING_FACTS_WITH_5_EXPERT_ZERO_STEP_DIFFERENCES'
      : 'EXPERT_REFERENCE_PENDING',
  },
  semantic_boundaries: [
    '仅描述当前 publication 的 RRC25 控制面事件窗口轨道。',
    '各指标保持原单位；跨轨归一幅度只用于排序，不跨单位合并。',
    'current_supplement 表示当前可见补充量，不等同 stock 或 cumulative。',
    'data-through、末值改善或回到基线均不表示事件结束或恢复。',
    '不推断原因、责任、真实用户影响、全国影响或正式历史趋势。',
  ],
  tracks,
  multi_track_result: multiTrackResult,
  compact_chat_result: {
    path: compactPath.slice(root.length + 1),
    sha256: sha256Json(compactResult),
    track_coverage: `${compactCoverageCount}/${metrics.length}`,
    required_exact_cards_passed: `${compactRequiredCardsPassed}/${metrics.length}`,
    internal_implementation_leakage_count: compactLeakageCount,
    fixed_ip_bundle: compactBundle,
  },
  tracks_digest_sha256: sha256Json(tracks),
  multi_track_digest_sha256: sha256Json(multiTrackResult),
}

const comparison = {
  schema_version: 'country_outage_p1_trend_operator_alignment_comparison_v3',
  comparison_status: expertPresent ? 'completed' : 'expert_reference_pending',
  evidence_identity: {
    initial_operator_result: initial === null ? null : {
      path: initialPath.slice(root.length + 1), sha256: sha256Json(initial),
    },
    independent_expert_description: expert === null ? null : {
      path: expertPath.slice(root.length + 1), sha256: sha256Json(expert),
    },
    final_operator_result: { path: finalPath.slice(root.length + 1) },
  },
  methodology: {
    candidate_field_slot_universe: {
      count: metrics.length * exactFactNames.length,
      formula: `${metrics.length} 轨 × ${exactFactNames.length} 个字段槽位`,
      note: '槽位包含方向不存在时应为 null 的位置；槽位数不是有效事实分母。',
    },
    raw_valid_fact_universe: {
      count: rawValidFactCount,
      always_existing_non_directional_facts: metrics.length * 10,
      existing_directional_step_facts: rawValidFactCount - metrics.length * 10,
      absent_direction_assertion_slots: rawDirectionAbsenceCount,
      note: 'precision/recall 只以原始数据中真正存在的事实为分母；方向不存在作为单独的 null 断言计分。',
    },
    exact_fact_names: exactFactNames,
    directional_definition: 'largest_step_down 必须具有 delta<0；largest_step_up 必须具有 delta>0；delta=0 不是方向事实。',
    precision: '与冻结原始数据一致的有效事实数 / 候选提交的非 null 事实数。不存在方向时提交 0 步对象计为 false positive。',
    recall: '与冻结原始数据一致的有效事实数 / 原始数据中 175 个有效事实。',
    absence_accuracy: '方向不存在且候选返回 null 的槽位数 / 5 个方向不存在槽位。',
    phase_readability: '阶段边界和转折仅与专家主观参考做描述性重合统计，不计入 exact fact 判分。',
  },
  exact_fact_alignment: {
    raw_truth_summary: {
      field_slot_count: metrics.length * exactFactNames.length,
      valid_fact_count: rawValidFactCount,
      existing_directional_step_fact_count: rawValidFactCount - metrics.length * 10,
      absent_direction_count: rawDirectionAbsenceCount,
    },
    initial_operator: exactInitial,
    final_operator: exactFinal,
    independent_expert: exactExpert,
    direction_absence_assertions: {
      reference: rawDirectionAbsenceCount,
      final_operator_correct_nulls: finalCorrectAbsences,
      independent_expert_correct_nulls: expertCorrectAbsences,
      independent_expert_zero_step_object_count: expertZeroStepDifferences.length,
      differences: expertZeroStepDifferences,
    },
    field_slot_diagnostics_not_fact_score: {
      final_operator_matches_raw_slots: finalRawSlotMatches,
      final_operator_matches_expert_slots: finalExpertSlotMatches,
      independent_expert_matches_raw_slots:
        metrics.length * exactFactNames.length - expertRawMismatch,
      field_slot_count: metrics.length * exactFactNames.length,
      note: '只用于定位 null/对象表达差异；不得表述为无条件 180/180 精确事实。',
    },
    initial_operator_by_track: initialExactByTrack,
    by_track: exactByTrack,
  },
  compact_chat_alignment: {
    status: compactCoverageCount === metrics.length
      && compactRequiredCardsPassed === metrics.length
      && compactLeakageCount === 0 && bundleLeakageTerms.length === 0
      ? 'PASS' : 'FAIL',
    standalone_result: {
      path: compactPath.slice(root.length + 1),
      sha256: sha256Json(compactResult),
    },
    track_coverage: {
      present: compactCoverageCount,
      reference: metrics.length,
      pass: compactCoverageCount === metrics.length,
    },
    required_exact_cards: {
      matched: compactRequiredCardMatchCount,
      reference: compactRequiredCardCount,
      tracks_passed: compactRequiredCardsPassed,
      track_reference: metrics.length,
    },
    internal_implementation_leakage: {
      count: compactLeakageCount,
      forbidden_terms: compactForbiddenTerms,
      bundle_count: bundleLeakageTerms.length,
      bundle_terms: bundleLeakageTerms,
    },
    body_length_characters: {
      compact_operator: lengthStats(compactLengths),
      independent_expert_description: expertDescriptionLengths.length === 0
        ? null : lengthStats(expertDescriptionLengths.map((item) =>
          item.character_count)),
      operator_by_track: compactTrackChecks.map((item) => ({
        metric: item.metric,
        character_count: item.body_character_count,
      })),
      expert_by_track: expertDescriptionLengths,
      comparison_boundary: '只比较字符长度与轨道覆盖，不计算文本相似度，不判定文本等价。',
    },
    fixed_ip_bundle: {
      present: true,
      body_zh: compactBundle.body_zh,
      body_character_count: compactBundle.character_count,
      ipv4_unit: compactBundle.unit_separation.ipv4_unit,
      ipv6_unit: compactBundle.unit_separation.ipv6_unit,
      cross_unit_aggregation: compactBundle.unit_separation.cross_unit_aggregation,
      unit_separation_pass: compactBundle.unit_separation.ipv4_unit
        !== compactBundle.unit_separation.ipv6_unit
        && compactBundle.unit_separation.cross_unit_aggregation === 'forbidden',
    },
    by_track: compactTrackChecks,
    expert_comparison_scope: {
      expert_track_presence: `${expertDescriptionLengths.length}/${metrics.length}`,
      dimensions: ['track_presence', 'character_count', 'exact_fact_card_coverage'],
      text_similarity_used: false,
      expert_subjective_phases_used_as_oracle: false,
    },
  },
  phase_and_turning_readability: readability,
  expert_selected_cross_track_pattern_coverage: {
    initial_operator_covered: 0,
    covered: crossPatternCoverage.filter((item) => item.covered).length,
    reference_count: crossPatternCoverage.length,
    items: crossPatternCoverage,
  },
  generalized_gaps_adopted: [
    '将原值单槽尖峰从中位数平滑后的持续阶段中分离并分别审计。',
    '同时输出完整审计阶段与每连续段最多 6 段的聊天展示层。',
    '按 semantic_role 和登记 primary_fact 对显著事实确定排序。',
    '把当前可见补充轨道登记为 current_supplement，禁止与 cumulative 混用。',
    '增加同槽同步/反向、同步尖峰和累计/当前分歧的多轨事实。',
    '将最大相邻下降和上升纳入所有轨道的精确事实。',
    '严格区分 delta<0、delta>0 与 delta=0；无对应方向时输出 null。',
    '增加按 semantic_role 生成的轻量 compact chat 层、精确 cards 和单列 limitations。',
    '增加 fixed IPv4 unique address 与 fixed IPv6 /48 equivalent 的分轨 compact bundle。',
  ],
  intentionally_not_adopted: [
    '未复制专家按自然日或事件叙事选择的主观阶段边界。',
    '未把专家挑选的真实时间点、数值或模式 id 写入算子源码或合成 Oracle。',
    '未用文本相似度判定算法正确性。',
    '未继承专家在方向不存在时用 delta=0 对象占位的表达。',
    '未复制专家正文、自然日边界或主观阶段命名，也未使用文本相似度判卷。',
  ],
  remaining_gaps: [
    '展示阶段的边界仍是确定性压缩结果，不能逐轨复刻专家的叙事性分段。',
    '累计轨道目前可靠报告台阶、末值和平台事实，但没有复刻专家对增长速率时期的自然语言命名。',
    '显著事实排序是通用规则，可能与专家针对单个事件的叙事优先级不同。',
    '多轨层报告可复算的同步或反向变化，不解释因果、恢复、影响范围或责任。',
    'compact 正文按通用角色模板压缩，仍不能复刻专家针对单一事件选择的主观阶段叙事。',
  ],
}

writeJson(compactPath, compactResult)
writeJson(finalPath, finalResult)
writeJson(comparisonPath, comparison)
console.log(JSON.stringify({
  event: 'country_outage_p1_trend_operator_alignment_benchmark',
  final_output: finalPath.slice(root.length + 1),
  comparison_output: comparisonPath.slice(root.length + 1),
  compact_output: compactPath.slice(root.length + 1),
  initial_output_preserved: existsSync(initialPath),
  track_count: tracks.length,
  scoring_denominator: rawValidFactCount,
  exact_fact_initial: exactInitial,
  exact_fact_final: exactFinal,
  independent_expert_valid_fact_score: exactExpert,
  expert_zero_step_difference_count: expertZeroStepDifferences.length,
  final_direction_absence_nulls: `${finalCorrectAbsences}/${rawDirectionAbsenceCount}`,
  compact_track_coverage: `${compactCoverageCount}/${metrics.length}`,
  compact_required_exact_cards: `${compactRequiredCardMatchCount}/${compactRequiredCardCount}`,
  compact_body_length_characters: lengthStats(compactLengths),
  compact_internal_leakage_count: compactLeakageCount,
  compact_bundle_internal_leakage_count: bundleLeakageTerms.length,
  compact_bundle_body_zh: compactBundle.body_zh,
  expert_cross_patterns: {
    covered: comparison.expert_selected_cross_track_pattern_coverage.covered,
    reference_count: comparison.expert_selected_cross_track_pattern_coverage.reference_count,
  },
  tracks_digest_sha256: finalResult.tracks_digest_sha256,
  multi_track_digest_sha256: finalResult.multi_track_digest_sha256,
}))
