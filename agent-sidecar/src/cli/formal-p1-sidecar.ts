export const FORMAL_P1_SIDECAR_RETIREMENT_CODE =
  'formal_p1_sidecar_retired' as const

export class FormalP1SidecarRetiredError extends Error {
  readonly code = FORMAL_P1_SIDECAR_RETIREMENT_CODE

  constructor() {
    super('旧正式 P1 Sidecar 已退役；该入口不再提供请求路由')
    this.name = 'FormalP1SidecarRetiredError'
  }
}

/**
 * 保留同名导出只用于让残留调用显式失败关闭；不会构造 HTTP handler 或 Server。
 */
export async function createFormalP1Sidecar(
  ..._retiredArguments: readonly unknown[]
): Promise<never> {
  throw new FormalP1SidecarRetiredError()
}

/**
 * 保留同名导出只用于让残留启动调用显式失败关闭；不会绑定任何端口。
 */
export async function startFormalP1Sidecar(
  ..._retiredArguments: readonly unknown[]
): Promise<never> {
  throw new FormalP1SidecarRetiredError()
}
