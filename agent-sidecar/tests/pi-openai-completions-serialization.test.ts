import assert from "node:assert/strict";
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";
import { resolve } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import type {
  CreateAgentSessionOptions,
  ModelRuntime,
} from "@earendil-works/pi-coding-agent";

import { assembleCountryOutageFacts } from "../src/domain/observation-assembler.js";
import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from "../src/formal-runtime-limits.js";
import {
  loadCountryOutageDependencyRiskException,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  PiReportNarrator,
  type CertifiedPiModelSelection,
  type FormalPiRunAuditRecord,
} from "../src/pi/index.js";
import type {
  CountryOutageReportDraft,
  ReportEvidenceBundle,
} from "../src/report/contracts.js";
import { buildDeterministicCountryOutageDraft } from "../src/report/deterministic-narrator.js";
import {
  buildCountryOutageModelLanguagePlan,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  type CountryOutageLanguageSlotId,
} from "../src/report/model-language-plan.js";
import {
  A4_REFERENCE,
  a4AsnPage,
  a4ObservationBatch,
} from "./helpers/a4-country-outage-fixture.js";

interface AdapterResult {
  stopReason: string;
  errorMessage?: string;
}

interface AdapterEventStream {
  result(): Promise<AdapterResult>;
}

interface OpenAICompletionsAdapterModule {
  streamSimple(
    model: unknown,
    context: unknown,
    options?: unknown,
  ): AdapterEventStream;
}

interface CapturedRequest {
  method: string | undefined;
  url: string | undefined;
  body: unknown;
}

const VALID_LANGUAGE_SLOT_TEXT: Readonly<
  Record<CountryOutageLanguageSlotId, string>
> = Object.freeze({
  "scope.denominator_explanation":
    "Prefix×VP 描述前缀与固定观测点之间的可见关系；它并非唯一前缀，也不能换算为用户或业务数量。",
  "assessment.evidence_boundary":
    "本报告只支持 BGP 控制面可见性描述，不能据此判断全国数据面状态，也无法认定用户或业务影响、事件原因和责任主体。",
  "address_families.impact_boundary":
    "地址族指标属于路由控制面观测，不能直接换算为用户、业务或实际流量影响。",
  "updates.causality_boundary":
    "相关 UPDATE 活动与可见性变化只构成时间对应；现有证据不足以据此证明因果关系。",
  "resources.resource_boundary":
    "等价资源表示规范化、去重后的路由资源覆盖，并非实际在线 IP 地址，也不能换算成用户或业务数量。",
});

function loopbackCertification(): CertifiedPiModelSelection {
  return {
    registryVersion: "loopback-model-registry-v1",
    profile: {
      id: "deepseek-v4-flash-loopback-v1",
      status: "certified",
      provider: "deepseek",
      model: "deepseek-v4-flash",
      modelVersion: "deepseek-v4-flash",
      expectedResponseModel: "deepseek-v4-flash",
      thinkingLevel: "off",
      piVersion: "0.82.1",
      certificationEvidenceId: "evidence:loopback-only",
      certifiedAt: "2026-07-30T00:00:00Z",
      modelRevisionKind: "mutable_alias",
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: "2099-01-01T00:00:00Z",
      certifiedScenarioSetId:
        "country-outage-rrc25-loopback-payload-v1",
      certifiedInputScope: "legal_country_outage_rrc25_v1",
    },
  };
}

function loopbackModel(
  port: number,
): NonNullable<CreateAgentSessionOptions["model"]> {
  return {
    id: "deepseek-v4-flash",
    name: "DeepSeek V4 Flash 本地完整载荷测试",
    api: "openai-completions",
    provider: "deepseek",
    baseUrl: `http://127.0.0.1:${port}/v1`,
    reasoning: false,
    input: ["text"],
    cost: {
      input: 0.14,
      output: 0.28,
      cacheRead: 0.0028,
      cacheWrite: 0,
    },
    contextWindow: 1_000_000,
    maxTokens: 16_384,
    compat: {
      supportsStore: false,
      supportsDeveloperRole: false,
      supportsUsageInStreaming: false,
      maxTokensField: "max_tokens",
    },
  };
}

function sseChunk(
  response: ServerResponse,
  body: Record<string, unknown>,
): void {
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "close",
  });
  response.end(
    [
      `data: ${JSON.stringify(body)}`,
      "",
      "data: [DONE]",
      "",
      "",
    ].join("\n"),
  );
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let byteLength = 0;

  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    byteLength += buffer.byteLength;
    if (byteLength > 1_048_576) {
      throw new Error("本地测试请求体超过 1 MiB 限制");
    }
    chunks.push(buffer);
  }

  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
}

test("真实 OpenAI-completions adapter 会序列化 onPayload 注入的 response_format", async () => {
  let capturedRequest: CapturedRequest | undefined;
  let handlerError: unknown;

  const server = createServer(async (request, response) => {
    try {
      capturedRequest = {
        method: request.method,
        url: request.url,
        body: await readJsonBody(request),
      };

      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "close",
      });
      response.end(
        [
          `data: ${JSON.stringify({
            id: "chatcmpl-loopback",
            object: "chat.completion.chunk",
            created: 0,
            model: "deepseek-v4-flash",
            choices: [
              {
                index: 0,
                delta: { role: "assistant", content: "{}" },
                finish_reason: "stop",
              },
            ],
          })}`,
          "",
          "data: [DONE]",
          "",
          "",
        ].join("\n"),
      );
    } catch (error) {
      handlerError = error;
      response.statusCode = 500;
      response.end("本地测试服务器无法读取请求");
    }
  });

  try {
    await new Promise<void>((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      server.listen(0, "127.0.0.1", () => {
        server.off("error", rejectListen);
        resolveListen();
      });
    });

    const address = server.address();
    assert.ok(address && typeof address !== "string");
    assert.equal((address as AddressInfo).address, "127.0.0.1");

    const adapterPath = resolve(
      process.cwd(),
      "node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js",
    );
    const adapter = (await import(
      pathToFileURL(adapterPath).href
    )) as OpenAICompletionsAdapterModule;

    let onPayloadCalls = 0;
    const stream = adapter.streamSimple(
      {
        id: "deepseek-v4-flash",
        name: "DeepSeek V4 Flash 本地序列化测试",
        api: "openai-completions",
        provider: "deepseek",
        baseUrl: `http://127.0.0.1:${(address as AddressInfo).port}/v1`,
        reasoning: false,
        input: ["text"],
        cost: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
        },
        contextWindow: 128_000,
        maxTokens: 1_024,
        compat: {
          supportsStore: false,
          supportsDeveloperRole: false,
          supportsUsageInStreaming: false,
          maxTokensField: "max_tokens",
        },
      },
      {
        messages: [
          {
            role: "user",
            content: [{ type: "text", text: "只用于本地序列化测试" }],
            timestamp: Date.now(),
          },
        ],
      },
      {
        apiKey: "local-loopback-test-key",
        onPayload(payload: unknown) {
          onPayloadCalls += 1;
          assert.ok(
            typeof payload === "object" && payload !== null,
            "adapter 交给 onPayload 的值应为对象",
          );
          return {
            ...payload,
            response_format: { type: "json_object" },
          };
        },
      },
    );

    const result = await stream.result();

    assert.equal(
      result.stopReason,
      "stop",
      result.errorMessage ?? "本地 adapter 调用应正常完成",
    );
    assert.equal(handlerError, undefined);
    assert.equal(onPayloadCalls, 1);
    assert.ok(capturedRequest, "loopback server 应捕获真实 adapter 请求");
    assert.equal(capturedRequest.method, "POST");
    assert.equal(capturedRequest.url, "/v1/chat/completions");
    assert.ok(
      typeof capturedRequest.body === "object" &&
        capturedRequest.body !== null,
      "POST 请求体应为 JSON 对象",
    );
    assert.deepEqual(
      (capturedRequest.body as Record<string, unknown>).response_format,
      { type: "json_object" },
    );
  } finally {
    if (server.listening) {
      await new Promise<void>((resolveClose, rejectClose) => {
        server.close((error) => {
          if (error) {
            rejectClose(error);
            return;
          }
          resolveClose();
        });
      });
    }
  }
});

test("完整 Pi 语言槽工具循环经真实 adapter 的每轮最终 payload 均小于 59904 bytes", async () => {
  const evidence: ReportEvidenceBundle = {
    facts: assembleCountryOutageFacts(a4ObservationBatch()),
    asnPages: [a4AsnPage()],
  };
  const plan = buildCountryOutageModelLanguagePlan(
    buildDeterministicCountryOutageDraft(evidence),
  );
  const languageBundleText = JSON.stringify({
    schemaVersion: COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
    slots: plan.map((item) => ({
      id: item.id,
      text: VALID_LANGUAGE_SLOT_TEXT[item.id],
    })),
  });
  const capturedRequests: CapturedRequest[] = [];
  let handlerError: unknown;
  let runtimeCalls = 0;
  let runtimeError: unknown;

  const server = createServer(async (request, response) => {
    try {
      capturedRequests.push({
        method: request.method,
        url: request.url,
        body: await readJsonBody(request),
      });
      const requestNumber = capturedRequests.length;
      const common = {
        id: `chatcmpl-loopback-${requestNumber}`,
        object: "chat.completion.chunk",
        created: 0,
        model: "deepseek-v4-flash",
        usage: {
          prompt_tokens: 1_000,
          completion_tokens: 20,
          total_tokens: 1_020,
        },
      };
      if (requestNumber <= 2) {
        const name =
          requestNumber === 1
            ? "country_outage_resolve"
            : "country_outage_get_observation";
        sseChunk(response, {
          ...common,
          choices: [
            {
              index: 0,
              delta: {
                role: "assistant",
                tool_calls: [
                  {
                    index: 0,
                    id: `loopback-tool-${requestNumber}`,
                    type: "function",
                    function: {
                      name,
                      arguments: "{}",
                    },
                  },
                ],
              },
              finish_reason: "tool_calls",
            },
          ],
        });
        return;
      }
      if (requestNumber === 3) {
        sseChunk(response, {
          ...common,
          choices: [
            {
              index: 0,
              delta: {
                role: "assistant",
                content: languageBundleText,
              },
              finish_reason: "stop",
            },
          ],
        });
        return;
      }
      response.statusCode = 500;
      response.end("不应出现第四次供应商请求");
    } catch (error) {
      handlerError = error;
      response.statusCode = 500;
      response.end("本地完整载荷测试失败");
    }
  });

  try {
    await new Promise<void>((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      server.listen(0, "127.0.0.1", () => {
        server.off("error", rejectListen);
        resolveListen();
      });
    });
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const port = (address as AddressInfo).port;
    const adapterPath = resolve(
      process.cwd(),
      "node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js",
    );
    const adapter = (await import(
      pathToFileURL(adapterPath).href
    )) as OpenAICompletionsAdapterModule;
    const modelRuntime = {
      hasConfiguredAuth() {
        return true;
      },
      async checkAuth() {
        return { configured: true };
      },
      isUsingOAuth() {
        return false;
      },
      streamSimple(model: unknown, context: unknown, options?: unknown) {
        runtimeCalls += 1;
        try {
          return adapter.streamSimple(model, context, {
            ...(options as Record<string, unknown> | undefined),
            apiKey: "local-loopback-test-key",
          });
        } catch (error) {
          runtimeError = error;
          throw error;
        }
      },
    } as unknown as ModelRuntime;
    const audits: FormalPiRunAuditRecord[] = [];
    const narrator = new PiReportNarrator({
      client: {
        async getObservationBatch() {
          throw new Error("固定证据模式不应重新读取");
        },
        async getAsns() {
          return structuredClone(a4AsnPage());
        },
      },
      model: loopbackModel(port),
      modelRuntime,
      certification: loopbackCertification(),
      dependencyRiskException:
        loadCountryOutageDependencyRiskException({
          now: new Date("2026-08-01T00:00:00Z"),
        }),
      auditSink(record) {
        audits.push(record);
      },
      now: () => new Date("2026-08-01T00:00:00Z"),
    });

    let draft: CountryOutageReportDraft | undefined;
    try {
      draft = await narrator.generate({
        reference: A4_REFERENCE,
        evidence,
      });
    } catch (error) {
      if (handlerError !== undefined) throw handlerError;
      if (runtimeError !== undefined) throw runtimeError;
      if (error instanceof Error) {
        throw new Error(
          `${error.message}; runtime=${runtimeCalls}; captured=${capturedRequests.length}; audits=${JSON.stringify(audits)}`,
          { cause: error },
        );
      }
      throw error;
    }

    assert.equal(handlerError, undefined);
    assert.ok(draft);
    assert.equal(capturedRequests.length, 3);
    const payloadBytes = capturedRequests.map((request) =>
      Buffer.byteLength(JSON.stringify(request.body), "utf8"),
    );
    assert.equal(payloadBytes.length, 3);
    assert.ok(payloadBytes[0]! < 20_000);
    assert.ok(payloadBytes[1]! < 20_000);
    assert.ok(payloadBytes[2]! < 30_000);
    for (const [index, request] of capturedRequests.entries()) {
      assert.equal(request.method, "POST");
      assert.equal(request.url, "/v1/chat/completions");
      assert.ok(
        payloadBytes[index]! <=
          FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes,
      );
    }
    assert.equal(
      Math.max(...payloadBytes) <
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes,
      true,
    );
    assert.equal(
      (capturedRequests[0]!.body as Record<string, unknown>)
        .response_format,
      undefined,
    );
    assert.equal(
      (capturedRequests[1]!.body as Record<string, unknown>)
        .response_format,
      undefined,
    );
    assert.deepEqual(
      (capturedRequests[0]!.body as Record<string, unknown>)
        .tool_choice,
      {
        type: "function",
        function: { name: "country_outage_resolve" },
      },
    );
    assert.deepEqual(
      (capturedRequests[1]!.body as Record<string, unknown>)
        .tool_choice,
      {
        type: "function",
        function: { name: "country_outage_get_observation" },
      },
    );
    assert.deepEqual(
      (capturedRequests[2]!.body as Record<string, unknown>)
        .response_format,
      { type: "json_object" },
    );
    assert.equal(
      (capturedRequests[2]!.body as Record<string, unknown>)
        .tool_choice,
      "none",
    );
    assert.doesNotMatch(
      JSON.stringify(capturedRequests[2]!.body),
      /"highlights"|"unknowns"|"evidenceRefs"/,
    );
    for (const item of plan) {
      const section:
        | CountryOutageReportDraft["sections"][number]
        | undefined = draft.sections.find(
        (candidate) => candidate.id === item.sectionId,
      );
      assert.equal(
        section?.paragraphs[item.paragraphIndex]?.text,
        VALID_LANGUAGE_SLOT_TEXT[item.id],
      );
    }
    assert.equal(audits.length, 1);
    assert.equal(audits[0]?.outcome, "accepted");
    assert.equal(
      audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
      3,
    );
    assert.equal(
      audits[0]?.runtimeSecurity.structuredOutput
        .payloadPreparedCount,
      1,
    );
    assert.deepEqual(audits[0]?.narration, {
      mode: "deterministic-base-with-language-slots-v1",
      slotContractVersion:
        COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
      requestedSlotCount: plan.length,
      acceptedSlotCount: plan.length,
      baseV5: "passed",
      mergeInvariant: "passed",
      finalV5: "passed",
      modelOutputApplied: true,
    });
  } finally {
    if (server.listening) {
      await new Promise<void>((resolveClose, rejectClose) => {
        server.close((error) => {
          if (error) {
            rejectClose(error);
            return;
          }
          resolveClose();
        });
      });
    }
  }
});
