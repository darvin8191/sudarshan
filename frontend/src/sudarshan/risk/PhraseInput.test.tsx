/**
 * The confirm phrase box (M20.7, backlog 2a): masked until the owner asks to see it, with a
 * 48 dp toggle, and nothing in it a password manager or the keyboard takes for a password or a
 * word to learn. The four places that ask for the phrase each use it (their own tests check).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Label } from '@/components/ui/label'
import { MASKED } from '@/sudarshan/masked'
import { PhraseInput } from './PhraseInput'

function Box({ what }: { what?: string }) {
  const [value, setValue] = useState('')
  return (
    <>
      <Label htmlFor="box">Confirm phrase</Label>
      <PhraseInput id="box" value={value} onChange={setValue} what={what} />
    </>
  )
}

describe('the confirm phrase box', () => {
  it('shows dots until Show is tapped, and dots again after Hide', () => {
    render(<Box />)
    const box = screen.getByLabelText('Confirm phrase')
    fireEvent.change(box, { target: { value: 'peacock blue' } })
    expect(box).toHaveValue('peacock blue')
    expect(box.className).toContain(MASKED)

    fireEvent.click(screen.getByRole('button', { name: 'Show confirm phrase' }))
    expect(box.className).not.toContain('-webkit-text-security')
    fireEvent.click(screen.getByRole('button', { name: 'Hide confirm phrase' }))
    expect(box.className).toContain(MASKED)
  })

  it('has a toggle a thumb can hit, inside the box, that never submits a form', () => {
    render(<Box />)
    const toggle = screen.getByRole('button', { name: 'Show confirm phrase' })
    expect(toggle).toHaveClass('size-12') // 48 dp
    expect(toggle).toHaveAttribute('type', 'button')
    expect(screen.getByLabelText('Confirm phrase')).toHaveClass('pr-14') // text clear of it
  })

  it('offers a password manager and the keyboard nothing to keep', () => {
    const { container } = render(<Box />)
    const box = screen.getByLabelText('Confirm phrase')
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(0)
    expect(box).toHaveAttribute('type', 'text')
    expect(box).not.toHaveAttribute('name')
    expect(box).toHaveAttribute('autocomplete', 'off')
    expect(box).toHaveAttribute('autocorrect', 'off')
    expect(box).toHaveAttribute('spellcheck', 'false')
    expect(box).toHaveAttribute('data-1p-ignore')
    expect(box).toHaveAttribute('data-lpignore', 'true')
    expect(box).toHaveAttribute('autocapitalize', 'characters') // STOP is typed in capitals
  })

  it('names its own box in the toggle, so two on one card can be told apart', () => {
    render(<Box what="new phrase" />)
    expect(screen.getByRole('button', { name: 'Show new phrase' })).toBeInTheDocument()
  })
})
