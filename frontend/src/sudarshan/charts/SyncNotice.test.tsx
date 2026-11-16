/**
 * The strip the phone chart shows for its sync with the server (M4.18): the newest sentence, a
 * mark and a spoken label beside it so it is never told by colour alone, the way to put this
 * phone's copy back when there is one, a close button, and every control 48 px.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NoticeBoard } from './docSync'
import { SyncNotice } from './SyncNotice'

describe('the sync strip', () => {
  it('shows nothing until there is something to say', () => {
    const { container } = render(<SyncNotice board={new NoticeBoard()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says which copy was kept, with a mark and its label, and offers this phone’s', () => {
    const board = new NoticeBoard()
    const run = vi.fn()
    board.post({
      id: 'drawings:NSE:SBIN',
      tone: 'conflict',
      text: 'The drawings on SBIN were changed on another device.',
      action: { label: 'Use this phone’s copy', run },
    })
    render(<SyncNotice board={board} />)
    const strip = screen.getByRole('status')
    expect(strip).toHaveTextContent('Changed elsewhere: The drawings on SBIN were changed')
    expect(strip.querySelector('svg')).not.toBeNull()
    const use = screen.getByRole('button', { name: 'Use this phone’s copy' })
    expect(use.className).toContain('min-h-12')
    fireEvent.click(use)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('shows the newest sentence, one per document, and closes', () => {
    const board = new NoticeBoard()
    board.post({ id: 'a', tone: 'local', text: 'First.' })
    board.post({ id: 'b', tone: 'kept', text: 'Second.' })
    board.post({ id: 'a', tone: 'local', text: 'First again.' })
    render(<SyncNotice board={board} />)
    expect(screen.getByRole('status')).toHaveTextContent('On this phone only: First again.')
    const close = screen.getByRole('button', { name: 'Dismiss this note' })
    expect(close.className).toContain('size-12')
    fireEvent.click(close)
    expect(screen.getByRole('status')).toHaveTextContent('Saved: Second.')
  })
})
