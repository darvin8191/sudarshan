// SUDARSHAN P6 D2: a placeholder for a screen another lane builds (D5). It says in one
// sentence that the screen is not ready, so a route that is registered never shows a blank page.
// The lane that builds the screen replaces this file's body and keeps its name and default export.
export default function FamilyWatchlist() {
  return (
    <section className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-5 shadow-[var(--shadow-card)]">
      <h2 className="text-base font-bold">Watchlist</h2>
      <p className="text-sm text-[color:var(--text-secondary)]">
        This screen is not ready yet. It will appear here in a later update.
      </p>
    </section>
  )
}
