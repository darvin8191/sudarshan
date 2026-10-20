import { Link, useLocation } from 'react-router'
import { bottomNavItems, isActiveRoute } from '@/config/navigation'
import { cn } from '@/lib/utils'

/**
 * The phone's bottom bar (SUDARSHAN: the D look). White, with the current page
 * marked three ways: its icon sits in a tinted pill, and its label turns the
 * accent colour and bold, so the mark never rests on colour alone. The tint
 * and the accent are the brand's in Live and the violet in Analyze mode.
 */
export function MobileBottomNav() {
  const location = useLocation()

  return (
    <nav
      aria-label="Main"
      className="md:hidden fixed bottom-0 left-0 right-0 z-50 border-t bg-card safe-area-bottom"
    >
      <div
        className="grid h-[4.75rem] px-1"
        style={{ gridTemplateColumns: `repeat(${bottomNavItems.length}, minmax(0, 1fr))` }}
      >
        {bottomNavItems.map((item) => {
          const active = isActiveRoute(location.pathname, item.href)
          return (
            <Link
              key={item.href}
              to={item.href}
              className={cn(
                'flex min-h-12 flex-col items-center justify-center gap-1 rounded-xl text-xs outline-none transition-colors touch-manipulation focus-visible:ring-2 focus-visible:ring-ring',
                active
                  ? 'font-bold text-primary'
                  : 'font-semibold text-muted-foreground hover:text-foreground active:bg-muted'
              )}
              aria-current={active ? 'page' : undefined}
            >
              <span
                aria-hidden="true"
                className={cn(
                  'flex h-[1.875rem] w-14 items-center justify-center rounded-full transition-colors',
                  active && 'bg-[color:var(--primary-tint)]'
                )}
              >
                <item.icon className="size-[1.375rem]" strokeWidth={active ? 2.1 : 1.9} />
              </span>
              <span className="leading-none">{item.label}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
