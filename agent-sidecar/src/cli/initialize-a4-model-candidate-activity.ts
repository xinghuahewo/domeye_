import {
  initializeA4CandidateActivityLedger,
  PiModelCertificationError,
} from '../pi/index.js'

void initializeA4CandidateActivityLedger()
  .then(() => {
    process.stdout.write(
      'DeepSeek 候选认证活动零成本账本已初始化。\n',
    )
  })
  .catch((error: unknown) => {
    const message =
      error instanceof PiModelCertificationError
        ? error.message
        : 'DeepSeek 候选认证活动零成本账本初始化失败'
    process.stderr.write(`${message}\n`)
    process.exitCode = 1
  })
