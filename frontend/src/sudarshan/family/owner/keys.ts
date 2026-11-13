// SUDARSHAN P6 D7a: the query keys of the Family page, in one place so a write can name what it
// made stale.
export const MEMBERS_KEY = ['sudarshan', 'family-owner', 'members'] as const
export const STATE_KEY = ['sudarshan', 'family-owner', 'state'] as const
export const SHARES_KEY = ['sudarshan', 'family-owner', 'shares'] as const
export const AUDIT_KEY = ['sudarshan', 'family-owner', 'audit'] as const
export const devicesKey = (memberId: number) =>
  ['sudarshan', 'family-owner', 'devices', memberId] as const
