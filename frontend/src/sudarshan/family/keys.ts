// SUDARSHAN P6 D8: the query keys and sentences the family settings screens share.
export const DEVICES_KEY = ['family', 'devices'] as const
export const SETTINGS_KEY = ['family', 'settings'] as const

/** The service marks a device failing after repeated sends that did not arrive (design 6.7). */
export const FAILING_WORDS =
  'Notifications may not arrive on this phone. Turn them off and on again in Settings.'
