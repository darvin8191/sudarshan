// SUDARSHAN M20.5 (P8 U4): the language of TRINETRA's explanations: English, Hinglish or Gujarati,
// as TRINETRA writes them (Hinglish) until the owner picks another (owner decision 16). The choice
// is the owner's setting on the server, so the Telegram relay follows it too: one tap saves it once
// (never retried); a save another screen made first, or one that was not confirmed, is said and the
// settings are read again.
import { LoaderCircle, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { problemOf } from './api'
import { LANG_FALLBACK, LANG_NAMES, type Lang } from './types'
import { useSaveLanguage, useSignalsSettings } from './useSignals'

export interface LanguageChoice {
  /** The language shown now: the one being saved, else the saved one, else the state's. */
  lang: Lang
  saving: boolean
  /** The saved settings are read, so a choice can be saved. */
  ready: boolean
  problem: string | null
  pick: (lang: Lang) => void
}

/** The choice and its save, for a sheet that shows TRINETRA's text in it. */
export function useLanguageChoice(stateLang: Lang, enabled = true): LanguageChoice {
  const settings = useSignalsSettings(enabled)
  const save = useSaveLanguage()
  const [problem, setProblem] = useState<string | null>(null)
  const saved = settings.data?.lang ?? stateLang
  const lang = save.isPending && save.variables ? save.variables.lang : saved
  const readProblem = settings.isError ? problemOf(settings.error).message : null
  return {
    lang,
    saving: save.isPending,
    ready: settings.data !== undefined,
    problem: problem ?? readProblem,
    pick: (next: Lang) => {
      const current = settings.data
      if (save.isPending || !current || next === current.lang) return
      setProblem(null)
      save.mutate(
        { version: current.version, lang: next },
        { onError: (error) => setProblem(problemOf(error, 'write').message) }
      )
    },
  }
}

export function LanguagePicker({ choice }: { choice: LanguageChoice }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div
        role="radiogroup"
        aria-label="Explanations in"
        className="grid grid-cols-3 gap-1 rounded-[0.875rem] border bg-card p-1"
      >
        {LANG_FALLBACK.map((lang) => {
          const selected = lang === choice.lang
          const disabled = !choice.ready || choice.saving
          return (
            <label
              key={lang}
              data-selected={selected ? 'true' : undefined}
              className={cn(
                'flex min-h-12 items-center justify-center rounded-[0.625rem] px-1 text-center text-[0.875rem] has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50',
                selected
                  ? 'bg-[color:var(--brand)] font-bold text-white'
                  : 'font-semibold text-[color:var(--text-secondary)] hover:bg-accent',
                disabled && !selected && 'opacity-60'
              )}
            >
              <input
                type="radio"
                name="sd-trinetra-lang"
                className="sr-only"
                checked={selected}
                disabled={disabled}
                onChange={() => choice.pick(lang)}
              />
              {LANG_NAMES[lang]}
            </label>
          )
        })}
      </div>
      {choice.saving && (
        <output className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
          Saving your language
        </output>
      )}
      {choice.problem && !choice.saving && (
        <p
          role="alert"
          className="flex items-start gap-1.5 text-[0.8125rem] leading-snug font-medium text-[color:var(--warning-text)]"
        >
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          {choice.problem}
        </p>
      )}
    </div>
  )
}
