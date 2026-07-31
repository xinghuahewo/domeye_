import {
  PiModelCertificationError,
  readA4CandidateReadinessStatus,
} from '../pi/index.js'

void readA4CandidateReadinessStatus()
  .then((status) => {
    process.stdout.write(`${JSON.stringify(status)}\n`)
    if (!status.ready) process.exitCode = 1
  })
  .catch((error: unknown) => {
    const code =
      error instanceof PiModelCertificationError
        ? error.code
        : 'candidate_readiness_status_failed'
    const message =
      error instanceof PiModelCertificationError
        ? error.message
        : 'DeepSeek 候选只读 readiness 检查失败'
    process.stderr.write(
      `${JSON.stringify({
        event: 'country_outage_a4_candidate_readiness_failed',
        code,
        message,
        safety: {
          readOnly: true,
          credentialsRead: false,
          networkAccessed: false,
        },
      })}\n`,
    )
    process.exitCode = 1
  })
