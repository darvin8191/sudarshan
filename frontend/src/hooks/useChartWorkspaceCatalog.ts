import {
  createIndexedDbWorkspaceStorage,
  type IndexedDbWorkspaceStorage,
  type WorkspaceCatalog,
  WorkspaceRepository,
} from 'openalgo-charts/workspace'
import { useCallback, useEffect, useRef, useState } from 'react'
// SUDARSHAN M4.18: the server keeps the copy of record of the catalogue when it is switched on.
import {
  storageNotice,
  storageOnServer,
  syncedWorkspaceStorage,
  whenStorageOnServer,
} from '@/sudarshan/charts/workspaceStorage'

export interface ChartWorkspaceCatalogState {
  catalog: WorkspaceCatalog | null
  loading: boolean
  pending: boolean
  error: string | null
  /**
   * SUDARSHAN M4.18: which copy of the layouts was kept after settling with the server, in
   * words. Not an error, and it stays until `dismissNotice`: no later save clears it.
   */
  notice: string | null
  dismissNotice(): void
  /** SUDARSHAN M4.18: the layouts are kept on the server (true) or in this browser (false). */
  keptOnServer: boolean
  reload(): Promise<void>
  run<T>(operation: (repository: WorkspaceRepository) => Promise<T>): Promise<T>
}

interface Owner {
  account: string
  repository: WorkspaceRepository
  storage: IndexedDbWorkspaceStorage
  active: boolean
  pending: number
}
interface View {
  account: string | null
  catalog: WorkspaceCatalog | null
  loading: boolean
  pending: boolean
  error: string | null
  notice: string | null
  keptOnServer: boolean
}

function browserStorage(): IndexedDbWorkspaceStorage {
  if (!window.indexedDB) throw new Error('Browser workspace storage is unavailable')
  // SUDARSHAN M4.18: this browser's storage stays as it was, with the server settled around it.
  return syncedWorkspaceStorage(createIndexedDbWorkspaceStorage(window.indexedDB))
}

const sessionError = () =>
  new Error('Chart workspace account or session changed. Retry in the current session.')
const errorMessage = (error: unknown) => (error instanceof Error ? error.message : String(error))
const empty = (account: string | null): View => ({
  account,
  catalog: null,
  loading: account !== null,
  pending: false,
  error: null,
  notice: null,
  keptOnServer: false,
})

/** Each mounted account owns its repository and every pending completion. */
export function useChartWorkspaceCatalog(
  account: string | null,
  storageFactory: () => IndexedDbWorkspaceStorage = browserStorage
): ChartWorkspaceCatalogState {
  const ownerRef = useRef<Owner | null>(null)
  const [view, setView] = useState<View>(() => empty(account))

  const owns = useCallback((owner: Owner) => owner.active && ownerRef.current === owner, [])
  const refresh = useCallback(
    async (owner: Owner) => {
      const catalog = await owner.repository.load()
      if (!owns(owner)) throw sessionError()
      // SUDARSHAN M4.18: which copy was kept, in words; a load with nothing to say keeps the
      // sentence already shown (review M3: the next autosave must not clear it).
      const said = storageNotice(owner.storage)
      const keptOnServer = storageOnServer(owner.storage)
      setView((previous) => ({
        ...previous,
        catalog:
          previous.catalog && previous.catalog.revision > catalog.revision
            ? previous.catalog
            : catalog,
        loading: false,
        notice: said ?? previous.notice,
        keptOnServer,
      }))
    },
    [owns]
  )

  useEffect(() => {
    setView(empty(account))
    if (account === null) return
    let storage: IndexedDbWorkspaceStorage | undefined
    let owner: Owner
    try {
      storage = storageFactory()
      owner = {
        account,
        storage,
        repository: new WorkspaceRepository(storage, `oa-trading:${account}`),
        active: true,
        pending: 0,
      }
      ownerRef.current = owner
    } catch (error) {
      void storage?.close().catch(() => {})
      setView({ ...empty(account), loading: false, error: errorMessage(error) })
      return
    }
    void refresh(owner).catch((error) => {
      if (owns(owner))
        setView((previous) => ({ ...previous, loading: false, error: errorMessage(error) }))
    })
    // SUDARSHAN (review L2): the first load never waits for the server's switch. When its answer
    // comes later and says on, the catalogue is loaded again so the server's copy is settled.
    const unhear = whenStorageOnServer(storage, () => {
      if (owns(owner)) void refresh(owner).catch(() => {})
    })
    return () => {
      unhear()
      owner.active = false
      if (ownerRef.current === owner) ownerRef.current = null
      void owner.storage.close().catch(() => {})
    }
  }, [account, storageFactory, refresh, owns])

  const currentOwner = useCallback(() => {
    const owner = ownerRef.current
    if (!owner || owner.account !== account || !owns(owner)) throw sessionError()
    return owner
  }, [account, owns])

  const reload = useCallback(async () => {
    const owner = currentOwner()
    setView((previous) => ({ ...previous, loading: true, error: null }))
    try {
      await refresh(owner)
    } catch (error) {
      if (!owns(owner)) throw sessionError()
      setView((previous) => ({ ...previous, loading: false, error: errorMessage(error) }))
      throw error
    }
  }, [currentOwner, owns, refresh])

  const run = useCallback(
    async <T>(operation: (repository: WorkspaceRepository) => Promise<T>): Promise<T> => {
      const owner = currentOwner()
      owner.pending++
      setView((previous) => ({ ...previous, pending: true, error: null }))
      try {
        const result = await operation(owner.repository)
        if (!owns(owner)) throw sessionError()
        await refresh(owner)
        return result
      } catch (error) {
        if (!owns(owner)) throw sessionError()
        setView((previous) => ({ ...previous, error: errorMessage(error) }))
        throw error
      } finally {
        owner.pending--
        if (owns(owner)) setView((previous) => ({ ...previous, pending: owner.pending > 0 }))
      }
    },
    [currentOwner, owns, refresh]
  )

  const dismissNotice = useCallback(
    () => setView((previous) => (previous.notice ? { ...previous, notice: null } : previous)),
    []
  )

  // Avoid showing the outgoing account's catalog before its effect cleanup runs.
  const visible = view.account === account ? view : empty(account)
  return {
    catalog: visible.catalog,
    loading: visible.loading,
    pending: visible.pending,
    error: visible.error,
    notice: visible.notice,
    dismissNotice,
    keptOnServer: visible.keptOnServer,
    reload,
    run,
  }
}
