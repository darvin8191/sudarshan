import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * A sheet that rises from the bottom of a phone screen: a modal dialog (focus
 * held inside; the close button, Escape or a tap outside closes it) with a
 * white card, an 18px top radius and a 48dp close button. On a wider screen it
 * sits centred at the bottom. It fades in over 160 ms and does not slide
 * (minimal motion); with reduced motion it simply appears.
 */
export function BottomSheet({
  open,
  onOpenChange,
  title,
  description,
  aside,
  children,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  /** Beside the title, before the close button: a figure, a badge. */
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 animate-[sd-fade-in_160ms_ease-out] bg-black/40" />
        <Dialog.Content
          className={cn(
            'fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] animate-[sd-fade-in_160ms_ease-out] flex-col rounded-t-[1.125rem] border-t bg-card pb-[env(safe-area-inset-bottom)] shadow-[0_-8px_30px_rgb(16_24_40/0.12)] outline-none',
            'md:inset-x-auto md:left-1/2 md:w-[30rem] md:-translate-x-1/2 md:border-x',
            className
          )}
        >
          <span
            aria-hidden="true"
            className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-[color:var(--border-subtle)]"
          />
          <div className="flex shrink-0 items-start gap-2 pt-1 pr-1 pl-4">
            <div className="flex min-w-0 flex-1 flex-col gap-0.5 pt-2.5">
              <Dialog.Title className="truncate text-lg leading-tight font-bold">
                {title}
              </Dialog.Title>
              {description ? (
                <Dialog.Description className="truncate text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
                  {description}
                </Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">Details and actions</Dialog.Description>
              )}
            </div>
            {aside}
            <Dialog.Close className="flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
              <X aria-hidden="true" className="size-6" />
              <span className="sr-only">Close</span>
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-2 pb-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
