const RETIREMENT_CODE = 'country_outage_agent_p2_s1_w5_retired'

process.stderr.write(
  `${JSON.stringify({
    error: RETIREMENT_CODE,
    message:
      'P2-S1 W5 旧入口已退役；请使用 start:interactive-agent。',
  })}\n`,
)
process.exitCode = 78
