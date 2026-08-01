/**
 * Collie theme (F008, Step 47): light / dark / follow-system.
 *
 * The whole UI is painted from CSS variables, so dark mode is a `.dark`
 * class on <html> that swaps the palette (Collie Dark, spec §6). The
 * preference lives in localStorage; 'system' tracks the OS setting live.
 */

export type ThemePreference = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'collie.theme'
const media = (): MediaQueryList => window.matchMedia('(prefers-color-scheme: dark)')

export function getThemePreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function resolveTheme(pref: ThemePreference = getThemePreference()): 'light' | 'dark' {
  if (pref === 'system') return media().matches ? 'dark' : 'light'
  return pref
}

function apply(pref: ThemePreference): void {
  const root = document.documentElement
  const dark = resolveTheme(pref) === 'dark'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
}

export function setThemePreference(pref: ThemePreference): void {
  try {
    if (pref === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, pref)
  } catch {
    // storage unavailable — still apply for this session
  }
  apply(pref)
}

/** Apply on boot and follow OS changes while preference is 'system'. */
export function initTheme(): () => void {
  apply(getThemePreference())
  applyFontScale(getFontScale())
  const listener = (): void => {
    if (getThemePreference() === 'system') apply('system')
  }
  media().addEventListener('change', listener)
  return () => media().removeEventListener('change', listener)
}

/* ── Text size (F/Accessibility, Step 48) ─────────────────────────────── */

export type FontScale = 'normal' | 'large' | 'largest'

const FONT_KEY = 'collie.fontScale'

export function getFontScale(): FontScale {
  try {
    const value = localStorage.getItem(FONT_KEY)
    return value === 'large' || value === 'largest' ? value : 'normal'
  } catch {
    return 'normal'
  }
}

function applyFontScale(scale: FontScale): void {
  document.documentElement.dataset.fontScale = scale
}

export function setFontScale(scale: FontScale): void {
  try {
    if (scale === 'normal') localStorage.removeItem(FONT_KEY)
    else localStorage.setItem(FONT_KEY, scale)
  } catch {
    // storage unavailable — still apply for this session
  }
  applyFontScale(scale)
}
