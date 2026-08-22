/**
 * Collie i18n foundation (Step 50).
 *
 * Tiny, dependency-free: locale dictionaries are TS modules, `t()` looks up
 * a key with English fallback and `{param}` interpolation. The preference
 * lives in localStorage ('system' follows the OS). `initI18n` stamps
 * lang/dir on <html> — the dir mechanism is the RTL foundation even though
 * none of the initial five locales are RTL.
 */

import { de } from './locales/de'
import { en } from './locales/en'
import { es } from './locales/es'
import { fr } from './locales/fr'
import { ja } from './locales/ja'
import { useEffect, useState } from 'react'

export type Locale = 'en' | 'de' | 'es' | 'fr' | 'ja'
export const LOCALES: readonly Locale[] = ['en', 'de', 'es', 'fr', 'ja']
export type LocalePreference = Locale | 'system'
export type TranslationKey = keyof typeof en

const STORAGE_KEY = 'collie.locale'
const CHANGE_EVENT = 'collie:locale-changed'
const RTL_LOCALES = new Set(['ar', 'he', 'fa', 'ur'])

const DICTIONARIES: Record<Locale, Partial<Record<TranslationKey, string>>> = {
  en,
  de,
  es,
  fr,
  ja
}

export const LOCALE_LABELS: Record<Locale, string> = {
  en: 'English',
  de: 'Deutsch',
  es: 'Español',
  fr: 'Français',
  ja: '日本語'
}

export function getLocalePreference(): LocalePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value && value in DICTIONARIES ? (value as Locale) : 'system'
  } catch {
    return 'system'
  }
}

export function resolveLocale(pref: LocalePreference = getLocalePreference()): Locale {
  if (pref !== 'system') return pref
  const wanted = (navigator.language || 'en').toLowerCase().split('-')[0]
  return wanted in DICTIONARIES ? (wanted as Locale) : 'en'
}

function stamp(): void {
  const locale = resolveLocale()
  document.documentElement.lang = locale
  document.documentElement.dir = RTL_LOCALES.has(locale) ? 'rtl' : 'ltr'
}

export function setLocalePreference(pref: LocalePreference): void {
  try {
    if (pref === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, pref)
  } catch {
    // storage unavailable — still switch for this session
  }
  stamp()
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function initI18n(): void {
  stamp()
}

export function t(key: TranslationKey, params?: Record<string, string | number>): string {
  const locale = resolveLocale()
  let text = DICTIONARIES[locale][key] ?? en[key] ?? key
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.replaceAll(`{${name}}`, String(value))
    }
  }
  return text
}

/** Re-render the component when the locale changes. */
export function useT(): typeof t {
  const [, force] = useState(0)
  useEffect(() => {
    const handler = (): void => force((v) => v + 1)
    window.addEventListener(CHANGE_EVENT, handler)
    return () => window.removeEventListener(CHANGE_EVENT, handler)
  }, [])
  return t
}
