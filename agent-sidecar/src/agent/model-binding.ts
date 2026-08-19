import { ModelRuntime } from '@earendil-works/pi-coding-agent'

import {
  assertFormalPiInstalledVersion,
  capCountryOutageModelOutput,
  createFrozenFormalCredentialStore,
  type FormalPiModelRuntimeFactory,
} from '../pi/formal-model-runtime.js'
import type { DomeyeFirstSliceModelBindingPayload } from './candidate-manifest.js'
import type { DomeyePiModelBinding } from './pi-interactive-agent-loop.js'

export type DomeyeModelBindingErrorCode =
  | 'runtime_initialization_failed'
  | 'runtime_metadata_invalid'
  | 'model_catalog_mismatch'
  | 'provider_auth_unavailable'
  | 'model_not_available'

export class DomeyeModelBindingError extends Error {
  constructor(readonly code: DomeyeModelBindingErrorCode) {
    super(code)
    this.name = 'DomeyeModelBindingError'
  }
}

function normalizedUrl(value: string): string {
  const url = new URL(value)
  return url.href.replace(/\/+$/, '')
}

export async function createDomeyePiModelBinding(
  options: {
    readonly identity: DomeyeFirstSliceModelBindingPayload
    readonly auth_path: string
    readonly runtime_factory?: FormalPiModelRuntimeFactory
  },
): Promise<DomeyePiModelBinding> {
  assertFormalPiInstalledVersion()
  const identity = structuredClone(options.identity)
  const credentials = createFrozenFormalCredentialStore(
    options.auth_path,
    identity.provider,
  )
  let runtime: ModelRuntime
  try {
    runtime = await (
      options.runtime_factory
      ?? (async (runtimeOptions) => await ModelRuntime.create(runtimeOptions))
    )({
      credentials,
      modelsPath: null,
      allowModelNetwork: false,
    })
  } catch {
    throw new DomeyeModelBindingError('runtime_initialization_failed')
  }
  if (runtime.getError()) {
    throw new DomeyeModelBindingError('runtime_metadata_invalid')
  }
  const catalogModel = runtime.getModel(identity.provider, identity.model)
  if (
    !catalogModel
    || catalogModel.provider !== identity.provider
    || catalogModel.id !== identity.model
    || catalogModel.api !== identity.api
    || normalizedUrl(catalogModel.baseUrl) !== normalizedUrl(identity.base_url)
  ) throw new DomeyeModelBindingError('model_catalog_mismatch')

  const model = capCountryOutageModelOutput(catalogModel)
  if (model.maxTokens !== identity.maximum_output_tokens) {
    throw new DomeyeModelBindingError('model_catalog_mismatch')
  }
  const auth = runtime.getProviderAuthStatus(identity.provider)
  if (!auth.configured || auth.source !== 'stored') {
    throw new DomeyeModelBindingError('provider_auth_unavailable')
  }
  let available: readonly typeof model[]
  try {
    available = await runtime.getAvailable(identity.provider)
  } catch {
    throw new DomeyeModelBindingError('runtime_metadata_invalid')
  }
  if (!available.some((item) =>
    item.provider === identity.provider && item.id === identity.model
  )) throw new DomeyeModelBindingError('model_not_available')
  if (runtime.getError()) {
    throw new DomeyeModelBindingError('runtime_metadata_invalid')
  }
  return Object.freeze({
    identity: Object.freeze(identity),
    model,
    model_runtime: runtime,
    thinking_level: identity.thinking_level,
  })
}
