#!/usr/bin/env node

import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

import {
  bindRealFirstSliceEvaluationTarget,
  finalizeAcceptanceRecordFiles,
  runFirstVerticalSliceEvaluation,
  writeEvaluationArtifacts,
} from './evaluator.mjs'

async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

function configPathArgument() {
  const path = process.argv[3]
  if (!path || process.argv.length !== 4) {
    throw new TypeError('用法: node run.mjs <run|accept> <config.json>')
  }
  return resolve(path)
}

async function runEvaluation(config, configDirectory) {
  if (config.schema_version !== 'domeye_first_slice_evaluation_run_config_v2') {
    throw new TypeError('run_config_schema_invalid')
  }
  if (!['pilot', 'formal'].includes(config.evaluation_phase)) {
    throw new TypeError('evaluation_phase_invalid')
  }
  const expectedRuns = config.evaluation_phase === 'pilot' ? 3 : 30
  if (config.runs !== expectedRuns) {
    throw new TypeError(
      config.evaluation_phase === 'pilot'
        ? 'pilot_runs_must_equal_3'
        : 'formal_runs_must_equal_30',
    )
  }
  const target = await bindRealFirstSliceEvaluationTarget(config.target)
  let journeyJudgments
  if (config.journey_judgments_path) {
    journeyJudgments = await readJson(resolve(
      configDirectory,
      config.journey_judgments_path,
    ))
  }
  const driveAdversarialCases = config.drive_adversarial_cases === true
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: target.loaded_candidate,
    run_j1_trial: target.run_j1_trial,
    execution_mode: target.execution_mode,
    runtime_principal_binding: target.runtime_principal_binding,
    runtime_source_binding: target.runtime_source_binding,
    evaluator_implementation: target.evaluator_implementation,
    api_endpoint_attestation: target.api_endpoint_attestation,
    evaluation_project_root: target.evaluation_project_root,
    execution_actor_id: config.execution_actor_id,
    evaluation_phase: config.evaluation_phase,
    runs: config.runs,
    ...(journeyJudgments ? { journey_judgments: journeyJudgments } : {}),
    ...(driveAdversarialCases ? { drive_adversarial_cases: true } : {}),
  })
  const output = await writeEvaluationArtifacts(
    result,
    resolve(configDirectory, config.output_directory),
  )
  return {
    event: 'domeye_first_slice_evaluation_written',
    candidate_id: result.summary.candidate_id,
    evaluation_run_id: result.summary.evaluation_run_id,
    evidence_gate: result.summary.evidence_gate.status,
    acceptance_state: 'pending_independent_review',
    paths: output.paths,
  }
}

async function acceptEvaluation(config, configDirectory) {
  if (config.schema_version !== 'domeye_first_slice_accept_config_v2') {
    throw new TypeError('accept_config_schema_invalid')
  }
  const record = await finalizeAcceptanceRecordFiles({
    summary_path: resolve(configDirectory, config.summary_path),
    evidence_jsonl_path: resolve(configDirectory, config.evidence_jsonl_path),
    independent_review_path: resolve(
      configDirectory,
      config.independent_review_path,
    ),
    output_path: resolve(configDirectory, config.output_path),
  })
  return {
    event: 'domeye_first_slice_acceptance_record_written',
    candidate_id: record.candidate_id,
    evaluation_run_id: record.evaluation_run_id,
    acceptance_state: record.acceptance_state,
    dg1_decision: record.dg1_decision,
    output_path: resolve(configDirectory, config.output_path),
  }
}

async function main() {
  const command = process.argv[2]
  if (!['run', 'accept'].includes(command)) {
    throw new TypeError('用法: node run.mjs <run|accept> <config.json>')
  }
  const configPath = configPathArgument()
  const config = await readJson(configPath)
  const result = command === 'run'
    ? await runEvaluation(config, dirname(configPath))
    : await acceptEvaluation(config, dirname(configPath))
  process.stdout.write(`${JSON.stringify(result)}\n`)
}

void main().catch((error) => {
  const code = error instanceof Error
    && /^[a-z][a-z0-9_:.-]{0,127}$/.test(error.message)
    ? error.message
    : 'evaluation_failed'
  process.stderr.write(`${JSON.stringify({
    event: 'domeye_first_slice_evaluation_failed',
    code,
  })}\n`)
  process.exitCode = 1
})
