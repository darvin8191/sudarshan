// SUDARSHAN P6 D2: a placeholder for a screen another lane builds (D3), shown outside the
// family shell because it is a public page. It says in one sentence that the screen is not
// ready. The lane that builds it replaces this file's body and keeps its name and default export.
import { BrandLockup } from '@/sudarshan/BrandLockup'
import { FamilyBadge } from '../FamilyBadge'

export default function FamilyLogin() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <section className="flex w-full max-w-sm flex-col items-start gap-3 rounded-[1.125rem] border bg-card px-5 py-6 shadow-[var(--shadow-card)]">
        <BrandLockup variant="stacked" align="start" />
        <FamilyBadge />
        <h1 className="sd-display text-[1.5rem] font-bold">Sign in</h1>
        <p className="text-sm text-[color:var(--text-secondary)]">
          Sign-in is not ready yet. Ask the account owner to check again soon.
        </p>
      </section>
    </div>
  )
}
