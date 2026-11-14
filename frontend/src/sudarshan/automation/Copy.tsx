import { Check, Copy as CopyIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

/** Copies `text` and says so for two seconds; says to copy by hand when the phone refuses. */
export function Copy({ text, what }: { text: string; what: string }) {
  const [said, setSaid] = useState<'copied' | 'refused' | null>(null)
  useEffect(() => {
    if (!said) return
    const timer = setTimeout(() => setSaid(null), 2000)
    return () => clearTimeout(timer)
  }, [said])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setSaid('copied')
    } catch {
      setSaid('refused')
    }
  }
  return (
    <Button type="button" variant="outline" className="h-12 self-start" onClick={copy}>
      {said === 'copied' ? (
        <Check aria-hidden="true" className="size-5" />
      ) : (
        <CopyIcon aria-hidden="true" className="size-5" />
      )}
      <span aria-live="polite">
        {said === 'copied'
          ? 'Copied'
          : said === 'refused'
            ? 'Not copied: select the text and copy it'
            : `Copy ${what}`}
      </span>
    </Button>
  )
}
