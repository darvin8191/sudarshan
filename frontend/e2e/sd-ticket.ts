import type { Locator } from '@playwright/test'

/**
 * What of the order ticket is cut (v0.16.0 phone review H2): a part that reaches past the
 * ticket's sides ("Total qty: 6" for 65, the Shares button, the ask size), a box that clips its
 * own words or figures ("SELL S..."), or words that run out of their own box (a button's label
 * wider than the button, its last letter white on the page). Empty when every part is whole.
 * Boxes a screen reader alone reads (1 px) are left out; the body scrolling up and down is not a
 * cut.
 */
export function ticketCuts(ticket: Locator): Promise<string[]> {
  return ticket.evaluate((dialog) => {
    const edge = dialog.getBoundingClientRect()
    const cut: string[] = []
    const past = (box: DOMRect) => box.left < edge.left - 0.5 || box.right > edge.right + 0.5
    for (const el of dialog.querySelectorAll<HTMLElement>('*')) {
      const box = el.getBoundingClientRect()
      if (box.width <= 1 || box.height <= 1) continue
      const style = getComputedStyle(el)
      if (style.visibility === 'hidden') continue
      const name = `${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 40)}"`
      if (past(box)) cut.push(`${name} reaches past the ticket's side`)
      else if (style.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) {
        cut.push(`${name} clips its own content`)
      }
    }
    const walker = document.createTreeWalker(dialog, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const words = (node.textContent ?? '').trim()
      const holder = node.parentElement
      if (!words || !holder) continue
      const box = holder.getBoundingClientRect()
      if (box.width <= 1 || box.height <= 1 || getComputedStyle(holder).visibility === 'hidden') continue
      const range = document.createRange()
      range.selectNodeContents(node)
      const ink = range.getBoundingClientRect()
      if (ink.width === 0) continue
      if (past(ink) || ink.left < box.left - 0.5 || ink.right > box.right + 0.5) {
        cut.push(`"${words.slice(0, 40)}" runs out of its box`)
      }
    }
    return cut
  })
}
