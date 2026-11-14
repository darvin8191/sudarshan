// SUDARSHAN P6 D7a (M2.9): the invite link, shown once. The page holds it in memory only while
// this sheet is open: it is never written to storage, the address bar or the query cache, and
// closing the sheet drops it. The owner sends it himself (WhatsApp, Telegram); SUDARSHAN never
// does. Copy puts it on the clipboard; Share opens Android's share sheet where there is one.
import { Check, Copy, Share2 } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { Invite } from '../types'
import { clockOf } from './words'

const JOIN = '/family/join/'

/** The link a member opens: this site's address and the invite's path. */
export function linkOf(invite: Invite, origin: string = window.location.origin): string {
  const path = invite.path.startsWith(JOIN) ? invite.path : `${JOIN}${invite.code}`
  return `${origin}${path}`
}

export function InviteSheet({
  invite,
  name,
  now,
  onClose,
}: {
  invite: Invite | null
  name: string
  now: number
  onClose: () => void
}) {
  const [copied, setCopied] = useState<'yes' | 'no' | null>(null)
  const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function'
  const expired = invite !== null && invite.expires_at <= now
  const link = invite ? linkOf(invite) : ''

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link)
      setCopied('yes')
    } catch {
      setCopied('no')
    }
  }
  const share = async () => {
    try {
      await navigator.share({ title: 'सुदर्शन · SUDARSHAN', url: link })
    } catch {
      // the owner closed the share sheet: nothing to say
    }
  }

  return (
    <BottomSheet
      open={invite !== null}
      onOpenChange={(open) => {
        if (!open) {
          setCopied(null)
          onClose()
        }
      }}
      title={`Invite link for ${name}`}
      description={
        invite
          ? expired
            ? `Expired at ${clockOf(invite.expires_at)}`
            : `Expires at ${clockOf(invite.expires_at)} · one use`
          : undefined
      }
    >
      {invite ? (
        <div className="flex flex-col gap-4">
          <p
            data-testid="invite-link"
            className="rounded-xl border bg-[color:var(--bg-canvas)] px-3 py-3 text-[0.875rem] break-all select-all"
          >
            {link}
          </p>
          <p className="text-[0.9375rem] leading-snug">
            Send it yourself by WhatsApp or Telegram. SUDARSHAN never sends it.
          </p>
          <p className="text-[0.9375rem] leading-snug text-[color:var(--text-secondary)]">
            This link is shown once. When you close this, it cannot be shown again; make a new
            invite if you need one.
          </p>
          {expired ? (
            <output className="block text-[0.9375rem] font-semibold text-[color:var(--warning-text)]">
              This link has expired. Close this and make a new invite.
            </output>
          ) : null}
          <div className="flex gap-2">
            <Button type="button" className="h-12 flex-1" disabled={expired} onClick={copy}>
              {copied === 'yes' ? (
                <Check aria-hidden="true" className="size-4" />
              ) : (
                <Copy aria-hidden="true" className="size-4" />
              )}
              Copy link
            </Button>
            {canShare ? (
              <Button
                type="button"
                variant="outline"
                className="h-12 flex-1"
                disabled={expired}
                onClick={share}
              >
                <Share2 aria-hidden="true" className="size-4" />
                Share
              </Button>
            ) : null}
          </div>
          {copied === 'yes' ? (
            <output className="block text-[0.9375rem] text-[color:var(--up)]">Link copied.</output>
          ) : null}
          {copied === 'no' ? (
            <output className="block text-[0.9375rem] text-[color:var(--warning-text)]">
              The link could not be copied. Press and hold it above to select and copy it.
            </output>
          ) : null}
        </div>
      ) : null}
    </BottomSheet>
  )
}
