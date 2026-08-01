import type { en } from './en'

/** Español */
export const es: Partial<Record<keyof typeof en, string>> = {
  'app.loading': 'Collie se está despertando...',
  'app.loadingSub': '*estira las patas, mueve la cola*',

  'sidebar.newChat': 'Nuevo chat',
  'sidebar.searchPlaceholder': 'Olfatear los chats...',
  'sidebar.searchLabel': 'Buscar conversaciones',
  'sidebar.empty': '¡Aún no hay nada! Empieza un chat — soy todo oídos.',
  'sidebar.noMatches': 'Sin resultados — mi olfato no encontró nada.',
  'sidebar.settings': 'Ajustes',
  'sidebar.delete': 'Borrar chat: {title}',
  'sidebar.working': 'Collie está trabajando en este chat',

  'chat.emptyTitle': '¡Hola! ¿Qué pasa?',
  'chat.emptySub': 'Pregúntame lo que sea — el tiempo, planes, ideas, o solo saluda.',
  'chat.inputPlaceholder': 'Pregúntame lo que sea...',
  'chat.inputLabel': 'Mensaje para Collie',
  'chat.send': 'Enviar mensaje',
  'chat.digUp': 'Desenterrar {count} mensajes anteriores ({hidden} enterrados)',

  'settings.title': '⚙️ Ajustes',
  'settings.back': 'Volver al chat',
  'settings.tabs.account': 'Cuenta',
  'settings.tabs.profile': 'Perfil',
  'settings.tabs.context': 'Contexto',
  'settings.tabs.memory': 'Memoria',
  'settings.tabs.subagents': 'Ayudantes',
  'settings.tabs.services': 'Servicios',
  'settings.tabs.automations': 'Automatizaciones',
  'settings.tabs.phone': 'Teléfono',
  'settings.tabs.pet': 'Mascota',

  'settings.appearance': 'Apariencia',
  'settings.theme.system': 'Como el sistema',
  'settings.theme.light': 'Claro',
  'settings.theme.dark': 'Oscuro',
  'settings.textSize': 'Tamaño del texto',
  'settings.textSize.normal': 'Normal',
  'settings.textSize.large': 'Grande',
  'settings.textSize.largest': 'Muy grande',
  'settings.language': 'Idioma',
  'settings.language.system': 'Como el sistema'
}
