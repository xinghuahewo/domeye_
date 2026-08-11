import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

import {
  CountryOutageDependencyRiskExceptionError,
  FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS,
  validateCountryOutageDependencyRiskException,
} from '../src/pi/dependency-risk-exception.js'

function validResource(): Record<string, unknown> {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        'resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json',
      ),
      'utf8',
    ),
  ) as Record<string, unknown>
}

test('依赖已升级后旧风险例外在到期前也不能重新激活', () => {
  assert.throws(
    () =>
      validateCountryOutageDependencyRiskException(
        validResource(),
        new Date('2026-08-12T15:59:59.999Z'),
      ),
    (error: unknown) =>
      error instanceof CountryOutageDependencyRiskExceptionError &&
      error.code === 'risk_exception_constraint_mismatch',
  )
})

test('advisory、组件、Pi 版本或正式路径约束漂移均失败关闭', async (context) => {
  const cases: Array<{
    name: string
    mutate: (resource: Record<string, unknown>) => void
  }> = [
    {
      name: 'advisory 漂移',
      mutate(resource) {
        ;(resource.risk as Record<string, unknown>).advisory =
          'GHSA-different'
      },
    },
    {
      name: '组件版本漂移',
      mutate(resource) {
        ;(resource.risk as Record<string, unknown>).component =
          'brace-expansion@5.0.8'
      },
    },
    {
      name: 'Pi 版本漂移',
      mutate(resource) {
        ;(resource.risk as Record<string, unknown>).piVersion =
          '0.83.0'
      },
    },
    {
      name: '擅自延长到期时间',
      mutate(resource) {
        resource.expiresAt = '2026-09-12T16:00:00Z'
      },
    },
    {
      name: 'PackageManager 被开启',
      mutate(resource) {
        ;(
          resource.constraints as Record<string, unknown>
        ).packageManagerResolutionEnabled = true
      },
    },
    {
      name: 'ModelResolver 被开启',
      mutate(resource) {
        ;(
          resource.constraints as Record<string, unknown>
        ).modelResolverEnabled = true
      },
    },
    {
      name: '外部 glob 被开启',
      mutate(resource) {
        ;(
          resource.constraints as Record<string, unknown>
        ).externalGlobEnabled = true
      },
    },
    {
      name: '增加第四个工具',
      mutate(resource) {
        ;(
          (
            resource.constraints as Record<string, unknown>
          ).allowedTools as string[]
        ).push('country_outage_arbitrary_tool')
      },
    },
    {
      name: '能力扩大被允许',
      mutate(resource) {
        ;(
          resource.constraints as Record<string, unknown>
        ).capabilityExpansionAllowed = true
      },
    },
    {
      name: 'responseModel 补丁摘要漂移',
      mutate(resource) {
        ;(
          (
            resource.constraints as Record<string, unknown>
          ).responseModelVendorPatch as Record<string, unknown>
        ).patchedSourceSha256 = 'f'.repeat(64)
      },
    },
    {
      name: 'responseModel 补丁应用模式漂移',
      mutate(resource) {
        ;(
          (
            resource.constraints as Record<string, unknown>
          ).responseModelVendorPatch as Record<string, unknown>
        ).applicationMode = 'uncontrolled'
      },
    },
    {
      name: '移除路径变化复评条件',
      mutate(resource) {
        resource.reevaluationTriggers = [
          'pi_fixed_version_available',
          'capability_scope_changed',
        ]
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      const resource = validResource()
      item.mutate(resource)
      assert.throws(
        () =>
          validateCountryOutageDependencyRiskException(
            resource,
            new Date('2026-08-01T00:00:00Z'),
          ),
        (error: unknown) =>
          error instanceof
            CountryOutageDependencyRiskExceptionError &&
          error.code === 'risk_exception_constraint_mismatch',
      )
    })
  }
})

test('代码冻结约束明确关闭解析和 glob，且只允许固定 Skill 与三个工具', () => {
  assert.deepEqual(
    FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS,
    {
      advisory: 'GHSA-mh99-v99m-4gvg',
      component: 'brace-expansion@5.0.7',
      piPackage: '@earendil-works/pi-coding-agent',
      piVersion: '0.84.1',
      resourceLoaderId: 'country-outage-static-resource-loader-v1',
      packageManagerResolutionEnabled: false,
      modelResolverEnabled: false,
      externalGlobEnabled: false,
      skillName: 'country-outage-report',
      allowedTools: [
        'country_outage_resolve',
        'country_outage_get_observation',
        'country_outage_get_asns',
      ],
      capabilityExpansionAllowed: false,
      responseModelVendorPatch: {
        patchId: 'pi-ai-openai-completions-response-model-v1',
        targetPackage: '@earendil-works/pi-ai',
        targetVersion: '0.84.1',
        targetRelativePathFromCodingAgent:
          'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',
        upstreamSourceSha256:
          '727d744f20985f667151e8ecee3ad30af388d9d66d91a92d0fb9ad3261da4363',
        patchedSourceSha256:
          '9bb5badc07dc1f073e094743acf4b81390601ae5bead8c35f15c54f7f0bc0504',
        patchArtifactSha256:
          'a7e89d8dae4ddb8a3aa2548153c2e0e68f57fd7b8102bdde10ecc8d297836c28',
        patchManifestSha256:
          'ba5f5bceae09c868285926d0b63c562f88168211284c52036aa62d8346bab1ad',
        sameNameResponseModelPreserved: true,
        applicationMode:
          'postinstall_exact_hash_replacement_v1',
      },
      reevaluationTriggers: [
        'pi_fixed_version_available',
        'formal_path_changed',
        'capability_scope_changed',
        'vendor_patch_drift',
        'vendor_patch_no_longer_required',
      ],
    },
  )
  assert.equal(
    Object.isFrozen(
      FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS,
    ),
    true,
  )
  assert.equal(
    Object.isFrozen(
      FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS.allowedTools,
    ),
    true,
  )
})
