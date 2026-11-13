// SUDARSHAN P6 D5 (M6.7): the member's view of the owner's trade book. Read only; the marked
// `readOnly` path of TradeCards carries it. Like Orders, it reads nothing for a member whose
// mask hides orders.

import { EmptyCard } from '@/sudarshan/books/cards/parts'
import { NOT_SHARED } from '@/sudarshan/books/cards/ReadOnlyOrders'
import { TradeCards } from '@/sudarshan/books/cards/TradeCards'
import type { MaskedTrade } from '../ordersView'
import { useFamilyMeOptional } from '../session'
import { useFamilyBoard } from '../useBoard'
import { useFamilyScreen } from '../useBook'

function Trades() {
  useFamilyScreen('trades')
  const board = useFamilyBoard<MaskedTrade>('trades')
  return <TradeCards {...board} />
}

export default function FamilyTrades() {
  const me = useFamilyMeOptional()
  if (me && (me.hidden.includes('orders') || !me.privacy.orders)) {
    return <EmptyCard title={NOT_SHARED} />
  }
  return <Trades />
}
