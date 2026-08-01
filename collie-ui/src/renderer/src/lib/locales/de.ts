import type { en } from './en'

/** Deutsch */
export const de: Partial<Record<keyof typeof en, string>> = {
  'app.loading': 'Collie wacht auf...',
  'app.loadingSub': '*streckt die Pfoten, wedelt mit dem Schwanz*',

  'sidebar.newChat': 'Neuer Chat',
  'sidebar.searchPlaceholder': 'Durch Chats schnüffeln...',
  'sidebar.searchLabel': 'Unterhaltungen durchsuchen',
  'sidebar.empty': 'Noch nichts hier! Fang einen Chat an — ich bin ganz Ohr.',
  'sidebar.noMatches': 'Keine Treffer — meine Nase hat nichts gefunden.',
  'sidebar.settings': 'Einstellungen',
  'sidebar.delete': 'Chat löschen: {title}',
  'sidebar.working': 'Collie arbeitet an diesem Chat',

  'chat.emptyTitle': 'Hey! Was gibt’s?',
  'chat.emptySub': 'Frag mich alles — Wetter, Pläne, Ideen, oder sag einfach hallo.',
  'chat.inputPlaceholder': 'Frag mich alles...',
  'chat.inputLabel': 'Nachricht an Collie',
  'chat.send': 'Nachricht senden',
  'chat.digUp': '{count} frühere Nachrichten ausbuddeln ({hidden} vergraben)',

  'settings.title': '⚙️ Einstellungen',
  'settings.back': 'Zurück zum Chat',
  'settings.tabs.account': 'Konto',
  'settings.tabs.profile': 'Profil',
  'settings.tabs.context': 'Kontext',
  'settings.tabs.memory': 'Gedächtnis',
  'settings.tabs.subagents': 'Helfer',
  'settings.tabs.services': 'Dienste',
  'settings.tabs.automations': 'Automationen',
  'settings.tabs.phone': 'Telefon',
  'settings.tabs.pet': 'Hund',

  'settings.appearance': 'Aussehen',
  'settings.theme.system': 'Wie System',
  'settings.theme.light': 'Hell',
  'settings.theme.dark': 'Dunkel',
  'settings.textSize': 'Textgröße',
  'settings.textSize.normal': 'Normal',
  'settings.textSize.large': 'Groß',
  'settings.textSize.largest': 'Am größten',
  'settings.language': 'Sprache',
  'settings.language.system': 'Wie System'
}
