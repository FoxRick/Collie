import type { en } from './en'

/** Français */
export const fr: Partial<Record<keyof typeof en, string>> = {
  'app.loading': 'Collie se réveille...',
  'app.loadingSub': "*s'étire les pattes, remue la queue*",

  'sidebar.newChat': 'Nouveau chat',
  'sidebar.searchPlaceholder': 'Flairer les discussions...',
  'sidebar.searchLabel': 'Rechercher des conversations',
  'sidebar.empty': "Rien ici pour l'instant ! Lance une discussion — je suis tout ouïe.",
  'sidebar.noMatches': "Aucun résultat — mon flair n'a rien trouvé.",
  'sidebar.settings': 'Réglages',
  'sidebar.delete': 'Supprimer la discussion : {title}',
  'sidebar.working': 'Collie travaille sur cette discussion',

  'chat.emptyTitle': 'Salut ! Quoi de neuf ?',
  'chat.emptySub': 'Demande-moi tout — météo, projets, idées, ou dis juste bonjour.',
  'chat.inputPlaceholder': 'Demande-moi tout...',
  'chat.inputLabel': 'Message pour Collie',
  'chat.send': 'Envoyer le message',
  'chat.digUp': 'Déterrer {count} messages plus anciens ({hidden} enfouis)',

  'settings.title': '⚙️ Réglages',
  'settings.back': 'Retour au chat',
  'settings.tabs.account': 'Compte',
  'settings.tabs.profile': 'Profil',
  'settings.tabs.context': 'Contexte',
  'settings.tabs.memory': 'Mémoire',
  'settings.tabs.subagents': 'Assistants',
  'settings.tabs.services': 'Services',
  'settings.tabs.automations': 'Automatisations',
  'settings.tabs.phone': 'Téléphone',
  'settings.tabs.pet': 'Chien',

  'settings.appearance': 'Apparence',
  'settings.theme.system': 'Comme le système',
  'settings.theme.light': 'Clair',
  'settings.theme.dark': 'Sombre',
  'settings.textSize': 'Taille du texte',
  'settings.textSize.normal': 'Normale',
  'settings.textSize.large': 'Grande',
  'settings.textSize.largest': 'Très grande',
  'settings.language': 'Langue',
  'settings.language.system': 'Comme le système'
}
