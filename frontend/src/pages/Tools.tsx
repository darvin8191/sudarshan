import { Link } from 'react-router'
import { type Tool, tools } from '@/lib/tools'
import { usePhoneLayout } from '@/sudarshan/chart/usePhoneLayout'

// SUDARSHAN M11.13 (T2): every tile on the brand tint with its tool's icon, in place of a palette
// colour per tile, and on a phone the tools laid out for it first, then the wide-screen pages
// under a heading that says so. The registry stays the one list (lib/tools.ts).

function ToolTile({ tool }: { tool: Tool }) {
  const Icon = tool.icon
  return (
    <Link
      to={tool.href}
      className="flex h-full min-h-12 items-start gap-3 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)] outline-none hover:border-[color:var(--brand)] focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex size-10 shrink-0 items-center justify-center rounded-[0.75rem] bg-[color:var(--brand-tint)] text-[color:var(--brand)]">
        <Icon aria-hidden="true" className="size-5" />
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[0.9375rem] leading-tight font-bold">{tool.title}</span>
        <span className="text-[0.8125rem] leading-snug text-[color:var(--text-tertiary)]">
          {tool.description}
        </span>
      </span>
    </Link>
  )
}

function Grid({ items }: { items: Tool[] }) {
  return (
    <ul className="grid grid-cols-1 gap-2.5 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
      {items.map((tool) => (
        <li key={tool.href}>
          <ToolTile tool={tool} />
        </li>
      ))}
    </ul>
  )
}

export default function Tools() {
  const phone = usePhoneLayout()
  const forPhone = tools.filter((tool) => tool.phone)
  const wide = tools.filter((tool) => !tool.phone)
  return (
    <div className="flex flex-col gap-4 md:gap-6 md:py-2">
      <div>
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">Tools</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Analytical tools for options trading and market analysis
        </p>
      </div>
      {phone ? (
        <>
          {forPhone.length > 0 && (
            <section aria-labelledby="sd-tools-phone" className="flex flex-col gap-2">
              <h2 id="sd-tools-phone" className="text-[0.9375rem] font-bold">
                Made for the phone
              </h2>
              <Grid items={forPhone} />
            </section>
          )}
          <section aria-labelledby="sd-tools-wide" className="flex flex-col gap-2">
            <h2 id="sd-tools-wide" className="text-[0.9375rem] font-bold">
              Best on a wide screen
            </h2>
            <p className="-mt-1 text-[0.8125rem] text-[color:var(--text-tertiary)]">
              Laid out for a tablet or a computer.
            </p>
            <Grid items={wide} />
          </section>
        </>
      ) : (
        <Grid items={tools} />
      )}
    </div>
  )
}
