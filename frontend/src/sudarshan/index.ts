/**
 * The SUDARSHAN layer of the app shell, imported once from src/main.tsx right
 * after the upstream index.css. Keeping every fork-level stylesheet and startup
 * effect behind this one import is what keeps the upstream entry point a
 * one-line change.
 */
import './theme.css'
// Applies the colour-blind preference to <html> before React renders.
import './display'
