import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { MASKED, NOT_A_PASSWORD } from '@/sudarshan/masked'

/**
 * The confirm phrase box, the same wherever the phrase is typed (the kill switch sheets, a
 * loosened limit, a changed phrase): dots until the owner taps Show, as the Auto login page
 * masks its key, with the toggle a 48 dp target inside the box. Not type="password", which would
 * invite a password manager to fill in the login password here or to keep the phrase; the
 * keyboard's suggestions are off too. What is typed stays in the box: nothing here logs it,
 * toasts it or sends it anywhere but the server's phrase check.
 */
export function PhraseInput({
  id,
  value,
  onChange,
  describedBy,
  what = 'confirm phrase',
}: {
  id: string
  value: string
  onChange: (next: string) => void
  describedBy?: string
  /** The box's name in the toggle's words ("Show confirm phrase"), for two boxes on one card. */
  what?: string
}) {
  const [shown, setShown] = useState(false)
  return (
    <div className="relative">
      <Input
        id={id}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`h-12 pr-14 text-base ${shown ? '' : MASKED}`}
        aria-describedby={describedBy}
        {...NOT_A_PASSWORD}
        autoCapitalize="characters"
      />
      <Button
        type="button"
        variant="ghost"
        className="absolute top-0 right-0 size-12"
        onClick={() => setShown((now) => !now)}
        aria-label={`${shown ? 'Hide' : 'Show'} ${what}`}
      >
        {shown ? (
          <EyeOff aria-hidden="true" className="size-5" />
        ) : (
          <Eye aria-hidden="true" className="size-5" />
        )}
      </Button>
    </div>
  )
}
