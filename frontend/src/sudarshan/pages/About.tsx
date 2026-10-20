import { ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { BrandLockup } from '../BrandLockup'
import { UPSTREAM } from '../brand'
import { BUILD, type BuildInfo, commitUrl } from '../buildInfo'

interface Credit {
  name: string
  role: string
  notice?: string
  licence: string
  licenceUrl: string
  href?: string
}

/** What this build is made of, and on what terms (M1.16). */
const CREDITS: Credit[] = [
  {
    name: UPSTREAM.name,
    role: 'The trading platform this is built on',
    licence: 'GNU AGPL-3.0',
    licenceUrl: UPSTREAM.licenceUrl,
    href: UPSTREAM.repo,
  },
  {
    name: 'openalgo-charts',
    role: 'Charting engine',
    notice: 'Copyright 2026 OpenAlgo',
    licence: 'Apache-2.0',
    licenceUrl: 'https://www.apache.org/licenses/LICENSE-2.0',
  },
  {
    name: 'openalgo-script',
    role: 'OpenScript compiler',
    notice: 'Copyright 2026 OpenScript contributors',
    licence: 'Apache-2.0',
    licenceUrl: 'https://www.apache.org/licenses/LICENSE-2.0',
  },
  {
    name: 'Lightweight Charts',
    role: 'Charts on some tools pages',
    notice: 'Copyright 2023 TradingView, Inc.',
    licence: 'Apache-2.0',
    licenceUrl: 'https://www.apache.org/licenses/LICENSE-2.0',
  },
  {
    name: 'Barlow, Khand, Noto Sans Devanagari, Noto Sans Gujarati, Tiro Devanagari Sanskrit',
    role: 'Fonts, served from this server',
    notice:
      'Copyright the Barlow Project Authors, Indian Type Foundry, and the Noto and Indigo Project Authors',
    licence: 'SIL Open Font License 1.1',
    licenceUrl: '/docs/licenses/fonts-OFL.txt',
  },
  {
    name: 'Lucide',
    role: 'Icons',
    notice: 'Copyright Lucide Contributors',
    licence: 'ISC',
    licenceUrl: 'https://lucide.dev/license',
  },
]

function OutLink({ href, children }: { href: string; children: React.ReactNode }) {
  const external = /^https?:\/\//.test(href)
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
    >
      {children}
      {external && <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />}
    </a>
  )
}

/** The platform version the server reports, or null while unknown. */
function usePlatformVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    fetch('/auth/app-info')
      .then((response) => response.json())
      .then((data) => {
        if (alive && data?.status === 'success' && typeof data.version === 'string') {
          setVersion(data.version)
        }
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  return version
}

export function AboutView({ build }: { build: BuildInfo }) {
  const platform = usePlatformVersion()
  const commitLink = commitUrl(build)

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex flex-col items-center gap-3 py-4 text-center">
        <BrandLockup variant="stacked" />
        <p className="text-muted-foreground">A personal, self-hosted trading platform.</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>This build</CardTitle>
          <CardDescription>
            {build.release ? 'A published release.' : 'A development build, not a release.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[9rem_1fr]">
            <dt className="font-medium">Release</dt>
            <dd className="text-muted-foreground">{build.release ?? 'None'}</dd>
            <dt className="font-medium">Commit</dt>
            <dd className="text-muted-foreground">
              {build.commit && commitLink ? (
                <OutLink href={commitLink}>
                  <span className="font-mono">{build.commit.slice(0, 7)}</span>
                </OutLink>
              ) : build.commit ? (
                <span className="font-mono">{build.commit.slice(0, 7)}</span>
              ) : (
                'Not recorded'
              )}
            </dd>
            <dt className="font-medium">Built on</dt>
            <dd className="text-muted-foreground">
              {platform ? `${UPSTREAM.name} ${platform}` : UPSTREAM.name}
            </dd>
            <dt className="font-medium">Source code</dt>
            <dd className="text-muted-foreground">
              {build.source ? (
                <OutLink href={build.source}>{build.source.replace(/^https:\/\//, '')}</OutLink>
              ) : (
                'Not recorded in this build'
              )}
            </dd>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Licences</CardTitle>
          <CardDescription>
            Built on {UPSTREAM.name} ({UPSTREAM.licence}); this build's source is offered under the
            same licence.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-y">
            {CREDITS.map((credit) => (
              <li key={credit.name} className="space-y-1 py-3 first:pt-0 last:pb-0">
                <p className="font-medium">
                  {credit.href ? <OutLink href={credit.href}>{credit.name}</OutLink> : credit.name}
                </p>
                <p className="text-sm text-muted-foreground">
                  {credit.role}
                  {credit.notice ? `. ${credit.notice}.` : '.'}
                </p>
                <p className="text-sm">
                  <OutLink href={credit.licenceUrl}>{credit.licence}</OutLink>
                </p>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}

export default function About() {
  return <AboutView build={BUILD} />
}
