// SUDARSHAN M13.7, M20.5 (P8 U4): "Why": TRINETRA's own explanation of a reading or a change, in
// the language the owner picked (or the first one TRINETRA sent, said as such), with the inputs it
// chose to publish. TRINETRA's text is data: shown as plain text, never as markup.
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { BiasMark } from './BiasMark'
import { LanguagePicker, useLanguageChoice } from './LanguagePicker'
import { figureName, QualityNote } from './ReadingCard'
import {
  type Bias,
  CHANGE_WORDS,
  type Change,
  COLOUR_FOR_MS,
  type Lang,
  type Quality,
  type Reading,
  type ReadingInput,
  type Why,
} from './types'
import { confidenceWords, moduleName, timeWords, whyIn } from './words'

/** What the sheet explains: a current reading or a row of the timeline. */
export interface WhySubject {
  key: string
  module: string
  instrument: string
  label: string
  bias: Bias
  confidence: number | null
  quality: Quality
  /** "Reading of 10:42" or "Changed 10:42": when it stands from. */
  when: string
  /** A colour of bias may be shown (recent enough, not lapsed). */
  colour: boolean
  why: Why
  inputs: ReadingInput[]
}

export function readingSubject(reading: Reading, now: number, colour: boolean): WhySubject {
  return {
    key: `reading:${reading.module}:${reading.instrument}`,
    module: reading.module,
    instrument: reading.instrument,
    label: reading.label,
    bias: reading.bias,
    confidence: reading.confidence,
    quality: reading.quality,
    when: `Reading of ${timeWords(reading.as_of_ms, now)}`,
    colour,
    why: reading.why,
    inputs: reading.inputs,
  }
}

export function changeSubject(change: Change, now: number): WhySubject {
  return {
    key: `change:${change.id}`,
    module: change.module,
    instrument: change.instrument,
    label: change.to_label,
    bias: change.bias,
    confidence: change.confidence,
    quality: change.quality,
    when: `${CHANGE_WORDS[change.kind] ?? CHANGE_WORDS.state} ${timeWords(change.at_ms, now)}`,
    colour: now - change.at_ms < COLOUR_FOR_MS,
    why: change.why,
    inputs: change.inputs,
  }
}

/** The language tag of TRINETRA's text, for the screen reader and the fonts. */
const TEXT_LANG: Record<Lang, string> = { en: 'en', hi: 'hi-Latn', gu: 'gu' }

function Explanation({ subject, lang }: { subject: WhySubject; lang: Lang }) {
  const shown = whyIn(subject.why, lang)
  return (
    <div className="flex flex-col gap-1.5">
      {shown.note && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {shown.note}
        </p>
      )}
      {shown.text !== null && (
        <p
          lang={shown.lang ? TEXT_LANG[shown.lang] : undefined}
          data-testid="why-text"
          className="text-[0.9375rem] leading-relaxed break-words whitespace-pre-line"
        >
          {shown.text}
        </p>
      )}
    </div>
  )
}

export function WhySheet({
  subject,
  stateLang,
  onClose,
}: {
  subject: WhySubject | null
  stateLang: Lang
  onClose: () => void
}) {
  const open = subject !== null
  const choice = useLanguageChoice(stateLang, open)
  const figure = subject ? confidenceWords(subject.confidence) : null
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={subject ? `${moduleName(subject.module)} · ${subject.instrument}` : 'Why'}
      description={subject?.when}
      step={subject?.key}
    >
      {subject && (
        <div className="flex flex-col gap-3">
          <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[1rem]">
            <span className="font-semibold break-words">{subject.label}</span>
            <BiasMark bias={subject.bias} colour={subject.colour} />
            {figure !== null && (
              <span className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
                {figureName(subject.module).toLowerCase()} {figure}
              </span>
            )}
          </p>
          <QualityNote quality={subject.quality} />
          <section aria-label="Explanation" className="flex flex-col gap-2">
            <h3 className="text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
              Explanations in
            </h3>
            <LanguagePicker choice={choice} />
            <Explanation subject={subject} lang={choice.lang} />
          </section>
          <section aria-label="Inputs" className="flex flex-col gap-1">
            <h3 className="text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
              What TRINETRA read
            </h3>
            {subject.inputs.length === 0 ? (
              <p className="text-[0.875rem] text-[color:var(--text-tertiary)]">
                TRINETRA published no inputs with this reading.
              </p>
            ) : (
              <dl className="divide-y divide-[color:var(--divider)]">
                {subject.inputs.map((input, at) => (
                  <div
                    key={`${at}:${input.name}`}
                    className="flex min-h-10 items-start justify-between gap-3 py-1.5 text-[0.875rem]"
                  >
                    <dt className="min-w-0 break-words text-[color:var(--text-secondary)]">
                      {input.name}
                    </dt>
                    <dd className="shrink-0 text-right font-semibold break-words">{input.value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
      )}
    </BottomSheet>
  )
}
