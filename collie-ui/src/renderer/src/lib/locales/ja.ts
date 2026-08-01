import type { en } from './en'

/** 日本語 */
export const ja: Partial<Record<keyof typeof en, string>> = {
  'app.loading': 'コリーが目を覚ましています...',
  'app.loadingSub': '*前足を伸ばして、しっぽをフリフリ*',

  'sidebar.newChat': '新しいチャット',
  'sidebar.searchPlaceholder': 'チャットをクンクン探す...',
  'sidebar.searchLabel': '会話を検索',
  'sidebar.empty': 'まだ何もないよ！チャットを始めよう — 全部聞くよ。',
  'sidebar.noMatches': '見つからなかった — 鼻が空振りだったよ。',
  'sidebar.settings': '設定',
  'sidebar.delete': 'チャットを削除: {title}',
  'sidebar.working': 'コリーがこのチャットで作業中',

  'chat.emptyTitle': 'やあ！どうしたの？',
  'chat.emptySub': '何でも聞いてね — 天気、予定、アイデア、あいさつだけでも。',
  'chat.inputPlaceholder': '何でも聞いてね...',
  'chat.inputLabel': 'コリーへのメッセージ',
  'chat.send': 'メッセージを送信',
  'chat.digUp': '前のメッセージを{count}件掘り出す（{hidden}件埋まってるよ）',

  'settings.title': '⚙️ 設定',
  'settings.back': 'チャットに戻る',
  'settings.tabs.account': 'アカウント',
  'settings.tabs.profile': 'プロフィール',
  'settings.tabs.context': 'コンテキスト',
  'settings.tabs.memory': 'メモリー',
  'settings.tabs.subagents': 'サブエージェント',
  'settings.tabs.services': 'サービス',
  'settings.tabs.automations': '自動化',
  'settings.tabs.phone': 'スマホ',
  'settings.tabs.pet': 'ペット',

  'settings.appearance': '外観',
  'settings.theme.system': 'システムに合わせる',
  'settings.theme.light': 'ライト',
  'settings.theme.dark': 'ダーク',
  'settings.textSize': '文字サイズ',
  'settings.textSize.normal': '標準',
  'settings.textSize.large': '大',
  'settings.textSize.largest': '特大',
  'settings.language': '言語',
  'settings.language.system': 'システムに合わせる'
}
