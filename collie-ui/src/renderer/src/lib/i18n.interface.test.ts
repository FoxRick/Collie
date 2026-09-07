// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { LOCALES, setLocalePreference, t, ui } from './i18n'
import { interfaceCopy } from './locales/interface'

afterEach(() => setLocalePreference('en'))

describe('interface language', () => {
  it('covers every offered non-English locale for each shared label', () => {
    for (const translations of Object.values(interfaceCopy)) {
      expect(translations).toHaveLength(LOCALES.length - 1)
      for (const value of translations) expect(value.trim()).not.toBe('')
    }
  })

  it('switches feature navigation and settings copy with the persisted preference', () => {
    setLocalePreference('de')
    expect(t('sidebar.agents')).toBe('Agenten')
    expect(t('sidebar.routines')).toBe('Routinen')
    expect(ui('Models & API keys')).toBe('Modelle und API-Schlüssel')
    expect(ui('App language')).toBe('App-Sprache')
    expect(document.documentElement.lang).toBe('de')
    setLocalePreference('ja')
    expect(t('sidebar.skills')).toBe('スキル')
    expect(ui('App language')).toBe('アプリの言語')
    expect(ui('My named project')).toBe('My named project')
  })
})
