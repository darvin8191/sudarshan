/**
 * What this build is, for the About page (M1.16). The release workflow
 * (.github/workflows/sudarshan-build.yml) passes these in at build time; a local
 * or CI test build has none of them and says so. Each value is checked against
 * the shape it must have, so a stray environment variable cannot put arbitrary
 * text or a foreign link on the page.
 */
export interface BuildInfo {
  /** Release tag, vA.B.C, or null for a build that is not a release. */
  release: string | null
  /** Full commit hash the build was made from, or null when unknown. */
  commit: string | null
  /** The public repository this build's source is in, or null when unknown. */
  source: string | null
}

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

export function parseBuildInfo(env: Record<string, unknown>): BuildInfo {
  const release = text(env.VITE_SD_RELEASE)
  const commit = text(env.VITE_SD_COMMIT)
  const source = text(env.VITE_SD_SOURCE).replace(/\/+$/, '')
  return {
    release: /^v\d+\.\d+\.\d+$/.test(release) ? release : null,
    commit: /^[0-9a-f]{7,40}$/i.test(commit) ? commit.toLowerCase() : null,
    source: /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/.test(source) ? source : null,
  }
}

/** The commit on the source repository, when both are known. */
export function commitUrl(info: BuildInfo): string | null {
  return info.source && info.commit ? `${info.source}/commit/${info.commit}` : null
}

export const BUILD: BuildInfo = parseBuildInfo(import.meta.env)
