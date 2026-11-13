// SUDARSHAN P6 D5 (M6.7): the member's view of the owner's order book. Read only; the marked
// `readOnly` path of OrderCards carries it. A member whose mask hides orders is told so and
// nothing is read for them: the screen is absent from their menu, and a direct visit is not a
// way round it.

import { OrderCards } from '@/sudarshan/books/cards/OrderCards'
import { EmptyCard } from '@/sudarshan/books/cards/parts'
import { NOT_SHARED } from '@/sudarshan/books/cards/ReadOnlyOrders'
import type { MaskedOrder } from '../ordersView'
import { useFamilyMeOptional } from '../session'
import { useFamilyBoard } from '../useBoard'
import { useFamilyScreen } from '../useBook'

function Orders() {
  useFamilyScreen('orders')
  const board = useFamilyBoard<MaskedOrder>('orders')
  return <OrderCards {...board} />
}

export default function FamilyOrders() {
  const me = useFamilyMeOptional()
  if (me && (me.hidden.includes('orders') || !me.privacy.orders)) {
    return <EmptyCard title={NOT_SHARED} />
  }
  return <Orders />
}
