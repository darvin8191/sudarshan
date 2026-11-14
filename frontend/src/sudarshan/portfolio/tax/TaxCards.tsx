// SUDARSHAN P7 F1 (M7.27): the per-head cards of the tax working paper (design p7 9.1). Every
// figure and every sentence is the pack's own (sudarshan/tax); the only sum made here is the big
// figure of a card from the two taxable amounts the pack gives beside it.
import { List } from 'lucide-react'
import type { BusinessBlock, BusinessIncome, CapitalGains, TaxCarry } from '../types'
import { fyLabel, rateText, rs } from './format'
import { Card, Line, OUTLINE, Result, RULE, Sentences } from './parts'

const BUCKET_WORDS: Record<TaxCarry['bucket'], string> = {
  stcl: 'Short-term capital loss',
  ltcl: 'Long-term capital loss',
  business: 'F&O (business) loss',
  speculative: 'Intraday (speculative) loss',
}

/** Losses that go on to later years: the kind, the year of the loss, the amount, usable to. */
export function Carried({ rows }: { rows: readonly TaxCarry[] }) {
  if (rows.length === 0) return null
  return (
    <div className={RULE}>
      <h3 className="text-sm font-semibold">Carried forward</h3>
      {rows.map((row) => (
        <Line
          key={`${row.bucket}-${row.loss_fy}-${row.source}`}
          label={`${BUCKET_WORDS[row.bucket]} of ${fyLabel(row.loss_fy)}`}
          sub={`Usable up to ${fyLabel(row.last_fy)}${row.source === 'owner' ? ' · your figure' : ''}`}
        >
          {rs(row.paise)}
        </Line>
      ))}
    </div>
  )
}

export function CapitalGainsCard({ cg, onSchedule }: { cg: CapitalGains; onSchedule: () => void }) {
  const tax = cg.indicative_tax
  const left = cg.needs_fmv.length + cg.unknown_cost.length
  return (
    <Card
      title="Capital gains"
      big={rs(cg.taxable.st_paise + cg.taxable.lt_paise)}
      bigLabel="Taxable gain on shares and equity funds, after losses and the exemption"
    >
      <div className={RULE}>
        <h3 className="text-sm font-semibold">Short-term</h3>
        <Line label="Gains">{rs(cg.st_gain_paise)}</Line>
        <Line label="Losses">{rs(cg.st_loss_paise)}</Line>
        <Line label="Taxable" strong>
          {rs(cg.taxable.st_paise)}
        </Line>
      </div>
      <div className={RULE}>
        <h3 className="text-sm font-semibold">Long-term</h3>
        <Line label="Gains">{rs(cg.lt_gain_paise)}</Line>
        <Line label="Losses">{rs(cg.lt_loss_paise)}</Line>
        <Line label="Exemption used" sub={`of ${rs(cg.exemption.limit_paise)} a year`}>
          {rs(cg.exemption.used_paise)}
        </Line>
        <Line label="Taxable" strong>
          {rs(cg.taxable.lt_paise)}
        </Line>
      </div>
      <div className={RULE}>
        <Line label="Tax at the special rates" strong>
          {rs(tax.tax_paise)}
        </Line>
        <p className="text-xs text-[color:var(--text-tertiary)]">
          Plus {rs(tax.cess_paise)} {tax.cess_label}. Not your slab tax.
        </p>
        {tax.lines.map((line) => (
          <Line
            key={`${line.term}-${line.rate_bp}`}
            label={`${line.term === 'long' ? 'Long-term' : 'Short-term'} at ${rateText(line.rate_bp)}`}
            sub={`on ${rs(line.taxable_paise)}`}
          >
            {rs(line.tax_paise)}
          </Line>
        ))}
      </div>
      <Carried rows={cg.carried} />
      {left > 0 && (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {left} {left === 1 ? 'sale is' : 'sales are'} listed and in no total. Open the sales to
          see which.
        </p>
      )}
      <button type="button" onClick={onSchedule} className={OUTLINE}>
        <List aria-hidden="true" className="size-4" />
        Every sale of the year ({cg.schedule.length + left + cg.exempt_buybacks.length})
      </button>
    </Card>
  )
}

function Block({
  title,
  block,
  taxable,
}: {
  title: string
  block: BusinessBlock
  taxable: number
}) {
  return (
    <div className={RULE}>
      <h3 className="text-sm font-semibold">{title}</h3>
      <Line label="Profit or loss before charges">
        <Result paise={block.gross_paise} />
      </Line>
      <Line label="Charges" sub="Every part, STT included">
        {rs(block.charges_paise)}
      </Line>
      <Line label="After charges" strong>
        <Result paise={block.net_paise} />
      </Line>
      <Line label="Taxable after losses set off">{rs(taxable)}</Line>
      <Line label="Turnover">{rs(block.turnover_paise)}</Line>
    </div>
  )
}

export function BusinessCard({ business }: { business: BusinessIncome }) {
  const waiting = business.open.length
  const unsettled = business.unknown_settlement.length
  return (
    <Card
      title="Business income"
      big={
        <Result
          paise={business.non_speculative.net_paise + business.speculative.net_paise}
          className="justify-start"
        />
      }
      bigLabel="F&O and intraday together, after charges"
    >
      <Block
        title="F&O and other derivatives"
        block={business.non_speculative}
        taxable={business.taxable.non_speculative_paise}
      />
      <Block
        title="Intraday shares (speculative)"
        block={business.speculative}
        taxable={business.taxable.speculative_paise}
      />
      <div className={RULE}>
        <Line label="Turnover in all" sub={business.turnover_method_label}>
          {rs(business.turnover_paise)}
        </Line>
      </div>
      <Sentences lines={business.flags} warn />
      {business.differs.length > 0 && (
        <div className={RULE}>
          <h3 className="text-sm font-semibold">Differs from Upstox's report</h3>
          {business.differs.map((row) => (
            <Line
              key={row.scrip}
              label={row.scrip}
              sub={`Upstox says ${row.upstox_paise === null ? 'nothing' : rs(row.upstox_paise)}`}
            >
              {row.own_paise === null ? 'Not worked out' : <Result paise={row.own_paise} />}
            </Line>
          ))}
        </div>
      )}
      {(waiting > 0 || unsettled > 0) && (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {waiting > 0 &&
            `${waiting} ${waiting === 1 ? 'position is' : 'positions are'} still open and in no total until closed. `}
          {unsettled > 0 &&
            `${unsettled} ${unsettled === 1 ? 'future is' : 'futures are'} past expiry with no settlement price and in no total.`}
        </p>
      )}
      <Carried rows={business.carried} />
    </Card>
  )
}
