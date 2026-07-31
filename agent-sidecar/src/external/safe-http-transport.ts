import { lookup } from 'node:dns/promises'
import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { isIP } from 'node:net'

import type {
  ClientRequest,
  IncomingHttpHeaders,
  IncomingMessage,
  RequestOptions,
} from 'node:http'
import type { LookupFunction } from 'node:net'

import type {
  ExternalDnsAddress,
  ExternalDnsResolver,
  ExternalHttpRequest,
  ExternalHttpResponse,
  ExternalHttpTransport,
} from './contracts.js'
import { ExternalEvidenceSafetyError } from './errors.js'

export interface PinnedNodeRequestOptions extends RequestOptions {
  servername?: string
}

export type PinnedNodeRequestFactory = (
  protocol: 'http:' | 'https:',
  url: URL,
  options: PinnedNodeRequestOptions,
  onResponse: (response: IncomingMessage) => void,
) => ClientRequest

const defaultRequestFactory: PinnedNodeRequestFactory = (
  protocol,
  url,
  options,
  onResponse,
) => protocol === 'https:'
  ? httpsRequest(url, options, onResponse)
  : httpRequest(url, options, onResponse)

function normalizedHeaders(
  headers: IncomingHttpHeaders,
): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [name, value] of Object.entries(headers)) {
    if (value === undefined) continue
    result[name.toLowerCase()] = Array.isArray(value)
      ? value.join(', ')
      : String(value)
  }
  return result
}

export class NodeExternalDnsResolver implements ExternalDnsResolver {
  async resolve(hostname: string): Promise<readonly ExternalDnsAddress[]> {
    const addresses = await lookup(hostname, {
      all: true,
      verbatim: true,
    })
    return addresses.map((item) => ({
      address: item.address,
      family: item.family as 4 | 6,
    }))
  }
}

export class PinnedNodeHttpTransport implements ExternalHttpTransport {
  constructor(
    private readonly requestFactory: PinnedNodeRequestFactory =
      defaultRequestFactory,
  ) {}

  async request(input: ExternalHttpRequest): Promise<ExternalHttpResponse> {
    const address = input.addresses[0]
    if (!address) {
      throw new ExternalEvidenceSafetyError(
        'external_dns_empty',
        '外部来源域名没有可验证的公开地址',
        true,
      )
    }
    const lookupPinned: LookupFunction = (
      _hostname,
      options,
      callback,
    ) => {
      if (options.all) {
        callback(null, [{
          address: address.address,
          family: address.family,
        }])
        return
      }
      callback(null, address.address, address.family)
    }
    const protocol = input.url.protocol
    if (protocol !== 'http:' && protocol !== 'https:') {
      throw new ExternalEvidenceSafetyError(
        'external_scheme_blocked',
        '外部证据只允许公开 HTTP/HTTPS URL',
      )
    }
    const originalHostname = input.url.hostname.startsWith('[')
      ? input.url.hostname.slice(1, -1)
      : input.url.hostname
    const headers = Object.fromEntries(
      Object.entries(input.headers).filter(
        ([name]) => name.toLowerCase() !== 'host',
      ),
    )
    headers.Host = input.url.host

    return await new Promise<ExternalHttpResponse>((resolve, reject) => {
      const outgoing = this.requestFactory(
        protocol,
        input.url,
        {
          method: 'GET',
          headers,
          lookup: lookupPinned,
          signal: input.signal,
          maxHeaderSize: 32 * 1024,
          setHost: true,
          ...(protocol === 'https:' && isIP(originalHostname) === 0
            ? { servername: originalHostname }
            : {}),
        },
        (response) => {
          const headers = normalizedHeaders(response.headers)
          const declaredLength = Number(headers['content-length'] ?? '0')
          if (
            Number.isFinite(declaredLength) &&
            declaredLength > input.maximumBytes
          ) {
            response.destroy()
            reject(new ExternalEvidenceSafetyError(
              'external_response_too_large',
              `外部来源响应超过 ${input.maximumBytes} 字节限制`,
            ))
            return
          }
          const chunks: Buffer[] = []
          let byteLength = 0
          response.on('data', (chunk: Buffer | string) => {
            const value = Buffer.isBuffer(chunk)
              ? chunk
              : Buffer.from(chunk)
            byteLength += value.byteLength
            if (byteLength > input.maximumBytes) {
              const error = new ExternalEvidenceSafetyError(
                'external_response_too_large',
                `外部来源响应超过 ${input.maximumBytes} 字节限制`,
              )
              response.destroy()
              reject(error)
              return
            }
            chunks.push(value)
          })
          response.once('error', reject)
          response.once('end', () => {
            resolve({
              status: response.statusCode ?? 0,
              headers,
              body: Buffer.concat(chunks),
            })
          })
        },
      )
      outgoing.once('error', reject)
      outgoing.end()
    })
  }
}
