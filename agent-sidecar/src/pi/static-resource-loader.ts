import { dirname } from 'node:path'

import {
  createExtensionRuntime,
  createSyntheticSourceInfo,
  type ResourceLoader,
  type Skill,
} from '@earendil-works/pi-coding-agent'
import {
  hashCountryOutageSkillResources,
  loadTrustedCountryOutageSkillResources,
} from './country-outage-skill-bundle.js'

const TRUSTED_SKILL_NAME = 'country-outage-report'

export const STATIC_RESOURCE_LOADER_ID =
  'country-outage-static-resource-loader-v1' as const

export interface StaticCountryOutageResourceBundle {
  loader: ResourceLoader
  skillBundleSha256: string
  resourceLoaderId: typeof STATIC_RESOURCE_LOADER_ID
}

function frontmatterValue(text: string, key: string): string | undefined {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)
  if (!match?.[1]) return undefined
  const values = match[1]
    .split(/\r?\n/)
    .flatMap((line) => {
      const separator = line.indexOf(':')
      if (separator < 0) return []
      return line.slice(0, separator).trim() === key
        ? [line.slice(separator + 1).trim()]
        : []
    })
  return values.length === 1 && values[0] ? values[0] : undefined
}

class StaticCountryOutageResourceLoader implements ResourceLoader {
  readonly #skill: Skill
  readonly #systemPrompt: string
  readonly #extensionRuntime = createExtensionRuntime()

  constructor(skill: Skill, systemPrompt: string) {
    this.#skill = skill
    this.#systemPrompt = systemPrompt
  }

  getExtensions() {
    return {
      extensions: [],
      errors: [],
      runtime: this.#extensionRuntime,
    }
  }

  getSkills() {
    return { skills: [this.#skill], diagnostics: [] }
  }

  getPrompts() {
    return { prompts: [], diagnostics: [] }
  }

  getThemes() {
    return { themes: [], diagnostics: [] }
  }

  getAgentsFiles() {
    return { agentsFiles: [] }
  }

  getSystemPrompt(): string {
    return this.#systemPrompt
  }

  getAppendSystemPrompt(): string[] {
    return []
  }

  extendResources(
    paths: Parameters<ResourceLoader['extendResources']>[0],
  ): void {
    if (
      (paths.skillPaths?.length ?? 0) > 0 ||
      (paths.promptPaths?.length ?? 0) > 0 ||
      (paths.themePaths?.length ?? 0) > 0
    ) {
      throw new Error('正式国家中断 Agent 禁止运行时扩展资源')
    }
  }

  async reload(): Promise<void> {
    // 静态加载器没有发现、包解析或热重载路径。
  }
}

export function createStaticCountryOutageResourceBundle(
  skillPath: string,
  trustedSystemPrompt: string,
): StaticCountryOutageResourceBundle {
  const resources = loadTrustedCountryOutageSkillResources(skillPath)
  const normalizedSkillPath = resources[0]!.path
  const skillBaseDir = dirname(normalizedSkillPath)

  const skillText = resources[0]!.content
  const name = frontmatterValue(skillText, 'name')
  const description = frontmatterValue(skillText, 'description')
  if (
    name !== TRUSTED_SKILL_NAME ||
    !description ||
    description.length > 1024
  ) {
    throw new Error('受信任的国家中断 Skill 元数据无效')
  }

  const referenceKnowledge = resources
    .slice(1)
    .map(
      (resource) =>
        `<trusted_reference path="${resource.relativePath}">\n${resource.content.trim()}\n</trusted_reference>`,
    )
    .join('\n\n')
  const systemPrompt = `${trustedSystemPrompt}

以下项目知识已由宿主从固定 Skill 包静态加载。不得尝试通过文件系统再次读取：
<trusted_country_outage_project_knowledge>
${referenceKnowledge}
</trusted_country_outage_project_knowledge>`

  const skill: Skill = {
    name,
    description,
    filePath: normalizedSkillPath,
    baseDir: skillBaseDir,
    sourceInfo: createSyntheticSourceInfo(normalizedSkillPath, {
      source: STATIC_RESOURCE_LOADER_ID,
      scope: 'temporary',
      origin: 'top-level',
      baseDir: skillBaseDir,
    }),
    disableModelInvocation: false,
  }

  return {
    loader: new StaticCountryOutageResourceLoader(skill, systemPrompt),
    skillBundleSha256: hashCountryOutageSkillResources(resources),
    resourceLoaderId: STATIC_RESOURCE_LOADER_ID,
  }
}
