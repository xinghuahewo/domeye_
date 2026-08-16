import {
  isFormalPiRunRejectionCode,
  PiModelCertificationError,
  reconcileA4PreLedgerFailure,
} from '../pi/index.js'

class CandidateActivityReconciliationConfigurationError extends Error {
  constructor() {
    super('DeepSeek 候选认证活动补记配置无效')
    this.name =
      'CandidateActivityReconciliationConfigurationError'
  }
}

async function main(): Promise<void> {
  const rawCode =
    process.env.COUNTRY_OUTAGE_PI_PRE_LEDGER_REJECTION_CODE?.trim()
  if (rawCode && !isFormalPiRunRejectionCode(rawCode)) {
    throw new CandidateActivityReconciliationConfigurationError()
  }
  const formalRejectionCode =
    rawCode && isFormalPiRunRejectionCode(rawCode)
      ? rawCode
      : undefined
  const snapshot = await reconcileA4PreLedgerFailure({
    ...(formalRejectionCode
      ? { formalRejectionCode }
      : {}),
  })
  process.stdout.write(
    `${JSON.stringify({
      event:
        'country_outage_a4_model_candidate_pre_ledger_failure_reconciled',
      attemptedAt: null,
      providerRunInitiatedAtReconciliation: false,
      costBasis: 'worst_case_single_report_reservation',
      committedCostCny: snapshot.committedCostCny,
      remainingBudgetCny: snapshot.remainingBudgetCny,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const code =
    error instanceof PiModelCertificationError
      ? error.code
      : error instanceof
            CandidateActivityReconciliationConfigurationError
        ? 'candidate_activity_reconciliation_configuration_invalid'
        : 'candidate_activity_reconciliation_failed'
  const message =
    error instanceof PiModelCertificationError ||
    error instanceof
      CandidateActivityReconciliationConfigurationError
      ? error.message
      : 'DeepSeek 候选认证活动补记失败'
  process.stderr.write(
    `${JSON.stringify({
      event:
        'country_outage_a4_model_candidate_pre_ledger_failure_reconciliation_failed',
      code,
      message,
    })}\n`,
  )
  process.exitCode = 1
})
