// SUDARSHAN P6 D4: one part of the books, as the props a read-only board is given. The three
// screens that show books (Dashboard, Positions, Holdings) all read through this, so a part has
// one reading and one wording for a read that failed.
import { useMemo } from 'react'
import type { ReadOnlyBookProps } from '@/sudarshan/books/cards/readOnlyParts'
import { FamilyError, NO_ANSWER } from './api'
import { type RowBase, rowsOf } from './books'
import { useFamilyFeed } from './familyManager'
import { familyMarketOpen } from './feedLine'
import type { BookPart } from './types'
import { useFamilyBook } from './useBook'

export function useFamilyBoard<Row extends RowBase>(part: BookPart): ReadOnlyBookProps<Row> {
  const query = useFamilyBook(part)
  const feed = useFamilyFeed().state
  const book = query.data
  const rows = useMemo(() => rowsOf<Row>(book), [book])
  const marketOpen = useMemo(() => familyMarketOpen(feed), [feed])
  // A read that failed with rows already on screen keeps them (marked by the service's words or by
  // the read time); with none, the card says what happened and offers a retry.
  const error = query.error
    ? query.error instanceof FamilyError
      ? query.error.message
      : NO_ANSWER
    : null
  return {
    readOnly: true,
    rows,
    readAt: book?.read_at ?? null,
    hidden: book?.hidden ?? [],
    state: book?.state ?? 'ok',
    words: book?.words ?? null,
    loading: query.isPending,
    error,
    onRetry: () => void query.refetch(),
    marketOpen,
  }
}
