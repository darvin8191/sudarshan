import type { Account, Activity, Asset, TabInfo } from '../types'

export function tab(id: number, name: string, extra: Partial<TabInfo> = {}): TabInfo {
  return {
    id,
    name,
    name_alt: null,
    kind: 'stocks',
    icon: 'briefcase',
    colour: 'brand',
    position: id,
    pinned: false,
    archived: false,
    system: false,
    manual_old_days: 30,
    version: 4,
    ...extra,
  }
}

export function entry(id: number, extra: Partial<Activity> = {}): Activity {
  return {
    id,
    tab_id: 2,
    seq: id,
    kind: 'BUY',
    status: 'posted',
    source: 'manual',
    trade_date: 20261104,
    account_id: 1,
    to_account_id: null,
    asset_id: 7,
    qty_u: 10_000_000,
    price_u: 14_282_500,
    amount_paise: 1_428_250,
    charges_paise: 0,
    charges_detail: null,
    tds_paise: 0,
    value_paise: null,
    ratio_num: null,
    ratio_den: null,
    lot_date: null,
    note: null,
    batch_id: null,
    created_at: 0,
    updated_at: 0,
    ...extra,
  }
}

export const ACCOUNT: Account = {
  id: 1,
  member_id: 1,
  name: 'Main demat',
  kind: 'demat',
  broker: null,
  note: null,
  system: false,
  version: 1,
}

export const ASSET: Asset = {
  id: 7,
  kind: 'listed',
  name: 'INFOSYS',
  exchange: 'NSE',
  symbol: 'INFY',
  isin: null,
  amfi_code: null,
  unit_label: null,
  asset_class: 'equity',
  sector: null,
  mcap: null,
  tags: [],
  aliases: [],
  tick_size_u: null,
  resolved_at: null,
  version: 1,
}
