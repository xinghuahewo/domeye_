import { createHash } from 'node:crypto'
import {
  existsSync,
  lstatSync,
  readFileSync,
  realpathSync,
} from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const MAX_RESOURCE_BYTES = 131_072

export const COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION =
  'country_outage_report_skill_v6' as const

export const COUNTRY_OUTAGE_SKILL_BUNDLE_FILES = [
  'SKILL.md',
  'references/metrics-and-boundaries.md',
  'references/report-output-contract.md',
] as const

export interface TrustedCountryOutageSkillResource {
  relativePath: (typeof COUNTRY_OUTAGE_SKILL_BUNDLE_FILES)[number]
  path: string
  content: string
}

function trustedRegularFile(path: string): string {
  const normalized = resolve(path)
  const stats = lstatSync(normalized)
  if (!stats.isFile() || stats.isSymbolicLink()) {
    throw new Error('受信任的国家中断 Skill 包含非普通文件')
  }
  if (realpathSync(normalized) !== normalized) {
    throw new Error('受信任的国家中断 Skill 不允许符号链接路径')
  }
  if (stats.size <= 0 || stats.size > MAX_RESOURCE_BYTES) {
    throw new Error('受信任的国家中断 Skill 资源大小无效')
  }
  return normalized
}

export function defaultCountryOutageSkillPath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/skills/country-outage-report/SKILL.md',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/skills/country-outage-report/SKILL.md',
    ),
  ]
  const selected = candidates.find((candidate) => existsSync(candidate))
  if (!selected) {
    throw new Error('找不到受信任的 country-outage-report Skill')
  }
  return selected
}

export function loadTrustedCountryOutageSkillResources(
  skillPath: string,
): readonly TrustedCountryOutageSkillResource[] {
  const normalizedSkillPath = trustedRegularFile(skillPath)
  const skillBaseDir = dirname(normalizedSkillPath)
  const resources = COUNTRY_OUTAGE_SKILL_BUNDLE_FILES.map(
    (relativePath) => {
      const path = trustedRegularFile(resolve(skillBaseDir, relativePath))
      return {
        relativePath,
        path,
        content: readFileSync(path, 'utf8'),
      }
    },
  )
  if (resources[0]?.path !== normalizedSkillPath) {
    throw new Error('受信任的国家中断 Skill 入口路径不一致')
  }
  return resources
}

export function hashCountryOutageSkillResources(
  resources: readonly Pick<
    TrustedCountryOutageSkillResource,
    'relativePath' | 'content'
  >[],
): string {
  if (
    resources.length !== COUNTRY_OUTAGE_SKILL_BUNDLE_FILES.length ||
    resources.some(
      (resource, index) =>
        resource.relativePath !==
        COUNTRY_OUTAGE_SKILL_BUNDLE_FILES[index],
    )
  ) {
    throw new Error('受信任的国家中断 Skill 资源集合或顺序无效')
  }
  const digest = createHash('sha256')
  for (const resource of resources) {
    digest.update(resource.relativePath)
    digest.update('\0')
    digest.update(resource.content)
    digest.update('\0')
  }
  return digest.digest('hex')
}

export function computeCountryOutageSkillBundleSha256(
  skillPath: string = defaultCountryOutageSkillPath(),
): string {
  return hashCountryOutageSkillResources(
    loadTrustedCountryOutageSkillResources(skillPath),
  )
}
