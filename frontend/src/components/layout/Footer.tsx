import { Monitor } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useSessionStore } from '@/stores/sessionStore'
import { BRAND, UPSTREAM } from '@/sudarshan/brand'

interface FooterProps {
  className?: string
}

export function Footer({ className }: FooterProps) {
  const [version, setVersion] = useState<string>('')
  const activeSessionCount = useSessionStore((s) => s.activeSessionCount)

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const response = await fetch('/auth/app-info')
        const data = await response.json()
        if (data.status === 'success') {
          setVersion(data.version)
        }
      } catch (_error) {}
    }

    fetchVersion()
  }, [])

  return (
    <footer className={cn('mt-auto border-t bg-muted/30', className)}>
      <div className="container mx-auto px-4 py-6">
        <div className="flex flex-col md:flex-row items-center justify-center gap-2 md:gap-4 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">
              <span lang="sa">{BRAND.devanagari}</span> / {BRAND.latin}
            </span>
            <span className="hidden md:inline">|</span>
            <Link to="/about" className="text-primary hover:underline font-medium">
              About
            </Link>
          </div>
          {/* AGPL-3.0: the platform this is built on stays credited. */}
          <span className="text-center">
            Built on{' '}
            <a
              href={UPSTREAM.repo}
              className="text-primary hover:underline font-medium"
              target="_blank"
              rel="noopener noreferrer"
            >
              {UPSTREAM.name}
            </a>{' '}
            ({UPSTREAM.licence})
          </span>
          {version && (
            <>
              <span className="hidden md:inline">|</span>
              <Badge variant="secondary" className="gap-1">
                <span className="opacity-75">v</span>
                <span>{version}</span>
              </Badge>
            </>
          )}
          {activeSessionCount > 0 && (
            <>
              <span className="hidden md:inline">|</span>
              <Badge variant="outline" className="gap-1">
                <Monitor className="h-3 w-3" />
                <span>
                  {activeSessionCount} {activeSessionCount === 1 ? 'session' : 'sessions'}
                </span>
              </Badge>
            </>
          )}
        </div>
      </div>
    </footer>
  )
}
