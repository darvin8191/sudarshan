// SUDARSHAN P6 D4: a figure the account owner keeps from this member (cross-design Y24). It is
// the word "Hidden" with an eye-off icon, never a blank and never a 0: a blank could be read as
// nothing there, and 0 as a flat position or a break-even day. Both the icon and the word are
// there, so it never rests on colour alone.
import { EyeOff } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Hidden({ className }: { className?: string }) {
  return (
    <span
      data-hidden="true"
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap text-muted-foreground',
        className
      )}
    >
      <EyeOff aria-hidden="true" className="size-3.5 shrink-0" />
      Hidden
    </span>
  )
}
