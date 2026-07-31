import type { AgentPublicError } from './contracts.js'

export class CountryOutageHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly nextAction?: string,
  ) {
    super(message)
    this.name = 'CountryOutageHttpError'
  }

  toPublicError(): AgentPublicError {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      ...(this.nextAction === undefined
        ? {}
        : { next_action: this.nextAction }),
    }
  }
}

export function publicErrorFromUnknown(
  error: unknown,
  fallbackCode: string,
  fallbackMessage: string,
): AgentPublicError {
  if (error instanceof CountryOutageHttpError) {
    return error.toPublicError()
  }
  if (error && typeof error === 'object') {
    const candidate = error as {
      code?: unknown
      message?: unknown
      retryable?: unknown
    }
    return {
      code:
        typeof candidate.code === 'string' && candidate.code
          ? candidate.code
          : fallbackCode,
      message:
        typeof candidate.message === 'string' && candidate.message
          ? candidate.message
          : fallbackMessage,
      retryable:
        typeof candidate.retryable === 'boolean'
          ? candidate.retryable
          : false,
    }
  }
  return {
    code: fallbackCode,
    message: fallbackMessage,
    retryable: false,
  }
}
