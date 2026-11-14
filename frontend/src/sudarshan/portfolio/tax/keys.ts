import { PORTFOLIO_KEY } from '../tab/ActivityTab'

/** Every query of the tax view: the papers and the owner's inputs. */
export const TAX_KEY = [...PORTFOLIO_KEY, 'tax'] as const
