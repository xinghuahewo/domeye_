import './source-loader.mjs'

const { canonicalJsonSha256 } = await import(
  '../../../agent-sidecar/src/shared/deterministic-json.ts'
)

export const FIRST_SLICE_ADVERSARIAL_CASES = Object.freeze({
  J2: Object.freeze([
    'J2-unauthorized-second-action',
  ]),
  J3: Object.freeze([
    'J3-tool-timeout',
    'J3-tool-failure',
    'J3-incomplete-series',
    'J3-wrong-identity',
    'J3-wrong-unit',
  ]),
  J4: Object.freeze([
    'J4-renderer-value-mutation',
    'J4-renderer-unit-mutation',
    'J4-renderer-missing-limitation',
    'J4-renderer-scope-expansion',
    'J4-renderer-cause-claim',
    'J4-renderer-recovery-claim',
  ]),
  J5: Object.freeze([
    'J5-tie-first-observation',
    'J5-null-not-zero',
    'J5-empty-observed-set',
    'J5-missing-slot',
    'J5-wrong-unit',
    'J5-wrong-publication',
    'J5-wrong-revision',
    'J5-wrong-window',
  ]),
})

export const FIRST_SLICE_ADVERSARIAL_CASE_SET = Object.freeze({
  schema_version: 'domeye_first_slice_adversarial_case_set_v1',
  anchor_contract_version: 'domeye.first-vertical-slice/v1.0',
  journeys: FIRST_SLICE_ADVERSARIAL_CASES,
})

export const FIRST_SLICE_ADVERSARIAL_CASE_SET_DIGEST =
  `sha256:${canonicalJsonSha256(FIRST_SLICE_ADVERSARIAL_CASE_SET)}`

export function isRegisteredJourneyCase(journeyId, caseId) {
  return FIRST_SLICE_ADVERSARIAL_CASES[journeyId]?.includes(caseId) === true
}
