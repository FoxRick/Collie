import { useEffect, useState } from 'react'
import {
  ArrowLeft,
  Brain,
  CircleUserRound,
  KeyRound,
  Mic2,
  Palette,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  UserRound
} from 'lucide-react'
import {
  collieClient,
  type ClearAllDataResult,
  type RuntimeStatus
} from '../lib/ipc'
import { useT, type TranslationKey } from '../lib/i18n'
import {
  getFontScale,
  getThemePreference,
  setFontScale,
  setThemePreference,
  type FontScale,
  type ThemePreference
} from '../lib/theme'
import {
  LOCALES,
  getLocalePreference,
  LOCALE_LABELS,
  resolveLocale,
  setLocalePreference,
  type LocalePreference
} from '../lib/i18n'
import CollieFace from '../components/CollieFace'
import ProfileTab from '../components/settings/ProfileTab'
import ContextTab from '../components/settings/ContextTab'
import MemoryTab from '../components/settings/MemoryTab'
import ServicesTab from '../components/settings/ServicesTab'
import PetTab from '../components/settings/PetTab'
import PhoneTab from '../components/settings/PhoneTab'
import SafetyApprovalsTab from '../components/settings/SafetyApprovalsTab'
import AccountTab from '../components/settings/AccountTab'
import ProviderManager from '../components/settings/ProviderManager'
import AudioInputTab from '../components/settings/AudioInputTab'
import UpdateTab from '../components/settings/UpdateTab'
import TabErrorBoundary from '../components/settings/TabErrorBoundary'
import type { AppView } from '../lib/navigation'

type Tab =
  | 'models'
  | 'account'
  | 'appearance'
  | 'audio'
  | 'profile'
  | 'context'
  | 'memory'
  | 'services'
  | 'phone'
  | 'pet'
  | 'safety'
  | 'onboarding'
  | 'updates'

// The Services and Desktop pet entries are reachable but not advertised in
// the sidebar: Services is a pointer to the main Connections directory (one
// click away in the sidebar already), and the pet is gated off this release.
// They stay renderable so deep links keep working — they just stop costing
// every user a dead nav slot.
const SETTINGS_GROUPS = [
  {
    label: 'Models',
    items: [{ key: 'models' as const, label: 'Models & API keys', icon: KeyRound }]
  },
  {
    label: 'Personalization',
    items: [
      { key: 'profile' as const, label: 'Collie personality', icon: CircleUserRound },
      { key: 'context' as const, label: 'Context', icon: UserRound },
      { key: 'memory' as const, label: 'Memory', icon: Brain }
    ]
  },
  {
    label: 'Connections',
    items: [{ key: 'phone' as const, label: 'Telegram', icon: Send }]
  },
  {
    label: 'Experience',
    items: [
      { key: 'audio' as const, label: 'Audio & input', icon: Mic2 },
      { key: 'appearance' as const, label: 'Appearance', icon: Palette }
    ]
  },
  {
    label: 'Account & safety',
    items: [
      { key: 'account' as const, label: 'Account', icon: CircleUserRound },
      { key: 'updates' as const, label: 'Updates', icon: RefreshCw },
      { key: 'onboarding' as const, label: 'Onboarding', icon: RotateCcw },
      { key: 'safety' as const, label: 'Safety & approvals', icon: ShieldCheck }
    ]
  }
]

interface Props {
  initialTab?: Tab
  onRedoOnboarding?: () => void
  onNavigate?: (view: AppView) => void
  /** Open the getting-started conversation (greeting + first chat). */
  onGetStarted?: () => void
}

export function clearAllDataNotice(result: ClearAllDataResult): string {
  if (result.cleared) return 'All clear. Fresh start!'

  const warningCount = result.warnings.length
  if (result.database_cleared) {
    const locations =
      warningCount === 1
        ? '1 local file or folder'
        : `${warningCount} local files or folders`
    return (
      `I cleared Collie's saved records, but couldn't remove ${locations}. ` +
      'Close apps using them, then try again.'
    )
  }

  return (
    "I couldn't clear Collie's database, so I left local files in place. " +
    'Close other Collie windows and try again.'
  )
}

const TAB_COPY: Record<Tab, { title: string; description: string }> = {
  models: { title: 'Models & API keys', description: 'Choose how Collie thinks and connects to AI providers.' },
  appearance: { title: 'Appearance', description: 'Make Collie comfortable to read and pleasant to use.' },
  audio: { title: 'Audio & input', description: 'Choose a microphone and make sure Collie can hear you.' },
  account: { title: 'Account', description: 'Manage your local data and account actions.' },
  profile: { title: 'Collie personality', description: 'Shape Collie’s voice, tone, and behavior.' },
  context: { title: 'Context', description: 'Give Collie durable instructions about your life and work.' },
  memory: { title: 'Memory', description: 'Review and control the facts Collie remembers.' },
  services: { title: 'Services', description: 'Connect the tools and accounts Collie can work with.' },
  phone: { title: 'Telegram', description: 'Keep Collie within reach when you are away from this app.' },
  pet: { title: 'Desktop pet', description: 'Your desktop companion is on the way — coming soon.' },
  safety: { title: 'Safety & approvals', description: 'Decide when Collie needs permission before acting.' },
  onboarding: {
    title: 'Onboarding',
    description: 'Walk through setup again without deleting your data.'
  },
  updates: { title: 'Updates', description: 'Check, download, and install Collie alpha releases.' }
}

export default function SettingsScreen({
  initialTab = 'models',
  onRedoOnboarding,
  onNavigate,
  onGetStarted
}: Props): React.JSX.Element {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [status, setStatus] = useState<RuntimeStatus>({})
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [oauth, setOauth] = useState<{ chatgpt: boolean; claude: boolean }>({
    chatgpt: false,
    claude: false
  })
  const [notice, setNotice] = useState('')
  // Danger-zone confirm state: the panel starts EMPTY — the user types
  // DELETE themselves. Prefilling would make it a lazy extra click.
  const [dangerOpen, setDangerOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState('')
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [theme, setTheme] = useState<ThemePreference>(getThemePreference())
  const [fontScale, setFontScaleState] = useState<FontScale>(getFontScale())
  const [locale, setLocaleState] = useState<LocalePreference>(getLocalePreference())
  const t = useT()

  const refresh = async (): Promise<void> => {
    try {
      const [statusData, settingsData, chatgpt, claude] = await Promise.all([
        collieClient.getStatus(),
        collieClient.getSettings(),
        collieClient.authStatus('chatgpt').catch(() => ({ signed_in: false })),
        collieClient.authStatus('claude').catch(() => ({ signed_in: false }))
      ])
      setStatus(statusData)
      setSettings(settingsData.settings)
      setOauth({ chatgpt: chatgpt.signed_in, claude: claude.signed_in })
    } catch {
      // core offline; screen shows placeholders
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const renderTab = () => {
    switch (tab) {
      case 'models':
        return (
          <ProviderManager
            status={status}
            settings={settings}
            oauth={oauth}
            onRefresh={refresh}
            onNotice={setNotice}
          />
        )
      case 'appearance':
        return (
            <section className="settings-card settings-control-card">
              <h3 className="mb-2 font-medium">{t('settings.appearance')}</h3>
              <div className="flex gap-2" role="radiogroup" aria-label={t('settings.appearance')}>
                {(['system', 'light', 'dark'] as const).map((pref) => (
                  <button
                    key={pref}
                    role="radio"
                    aria-checked={theme === pref}
                    onClick={() => {
                      setThemePreference(pref)
                      setTheme(pref)
                    }}
                    className={`settings-button ${theme === pref ? 'is-selected' : ''}`}
                  >
                    {t(`settings.theme.${pref}` as TranslationKey)}
                  </button>
                ))}
              </div>
              <h4 className="mb-2 mt-4 text-sm font-medium">{t('settings.textSize')}</h4>
              <div className="flex gap-2" role="radiogroup" aria-label={t('settings.textSize')}>
                {(['normal', 'large', 'largest'] as const).map((scale) => (
                  <button
                    key={scale}
                    role="radio"
                    aria-checked={fontScale === scale}
                    onClick={() => {
                      setFontScale(scale)
                      setFontScaleState(scale)
                    }}
                    className={`settings-button ${fontScale === scale ? 'is-selected' : ''}`}
                  >
                    {t(`settings.textSize.${scale}` as TranslationKey)}
                  </button>
                ))}
              </div>
              <h4 className="mb-2 mt-4 text-sm font-medium">Language</h4>
              <p className="mb-2 text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                Pick the language Collie speaks. “Automatic” follows your computer.
              </p>
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Language">
                {(['system', ...LOCALES] as const).map((pref) => {
                  const label =
                    pref === 'system'
                      ? `Automatic (${LOCALE_LABELS[resolveLocale()]})`
                      : LOCALE_LABELS[pref]
                  return (
                    <button
                      key={pref}
                      role="radio"
                      aria-checked={locale === pref}
                      onClick={() => {
                        setLocalePreference(pref as LocalePreference)
                        setLocaleState(pref as LocalePreference)
                      }}
                      className={`settings-button ${locale === pref ? 'is-selected' : ''}`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </section>
        )
      case 'audio':
        return <AudioInputTab />
      case 'account':
        return (
          <>
            <AccountTab />
            <section className="settings-card">
              <h3 className="mb-2 font-medium">Your data</h3>
              <p className="settings-lead">
                Take everything Collie knows with you, any time.
              </p>
              <div>
                <button
                  onClick={() =>
                    void collieClient
                      .exportData()
                      .then((r) => setNotice(`All packed up! Saved to ${r.path}`))
                      .catch((e) =>
                        setNotice(e instanceof Error ? e.message : 'Export failed.')
                      )
                  }
                  className="settings-button"
                >
                  Export my data
                </button>
              </div>
            </section>
            <section className="settings-card settings-danger-zone">
              <h3 className="mb-2 font-medium">Danger zone</h3>
              <p className="settings-lead">
                Deletes everything Collie remembers — chats, memories, people,
                automations. This cannot be undone.
              </p>
              {dangerOpen ? (
                <div className="settings-danger-confirm">
                  <label className="form-field">
                    <span>Type DELETE to confirm</span>
                    <input
                      autoFocus
                      value={confirmDelete}
                      onChange={(event) => setConfirmDelete(event.target.value)}
                      placeholder="DELETE"
                      aria-label="Type DELETE to confirm deleting all data"
                    />
                  </label>
                  <div className="flex gap-2">
                    <button
                      className="settings-button is-danger"
                      disabled={confirmDelete !== 'DELETE' || deleteBusy}
                      onClick={() => {
                        setDeleteBusy(true)
                        void collieClient
                          .clearAllData()
                          .then((result) => {
                            setNotice(clearAllDataNotice(result))
                            setDangerOpen(false)
                            setConfirmDelete('')
                          })
                          .catch((e) =>
                            setNotice(e instanceof Error ? e.message : 'Could not clear.')
                          )
                          .finally(() => setDeleteBusy(false))
                      }}
                    >
                      {deleteBusy ? 'Deleting…' : 'Delete everything'}
                    </button>
                    <button
                      className="settings-button"
                      onClick={() => {
                        setDangerOpen(false)
                        setConfirmDelete('')
                      }}
                      disabled={deleteBusy}
                    >
                      Keep my data
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <button
                    className="settings-button is-danger"
                    onClick={() => setDangerOpen(true)}
                  >
                    Delete all data…
                  </button>
                </div>
              )}
            </section>
          </>
        )
      case 'updates':
        return <UpdateTab />
      case 'onboarding':
        return (
          <div className="flex flex-col gap-4">
            <section className="settings-card onboarding-card">
              <div className="settings-card-icon"><RotateCcw size={19} /></div>
              <div>
                <h3>Getting started</h3>
                <p>
                  Reopen the welcome chat — the one where Collie asks your name.
                  It picks up right where you left off.
                </p>
              </div>
              <button
                className="settings-button is-primary"
                onClick={() => onGetStarted?.()}
              >
                Getting started
              </button>
            </section>
            <section className="settings-card onboarding-card">
              <div className="settings-card-icon"><RotateCcw size={19} /></div>
              <div>
                <h3>Run setup again</h3>
                <p>
                  Revisit provider sign-in and connection choices. Your chats, memories, and
                  existing settings stay intact.
                </p>
              </div>
              <button className="settings-button is-primary" onClick={onRedoOnboarding}>
                Start onboarding
              </button>
            </section>
          </div>
        )
      case 'safety':
        return <SafetyApprovalsTab />
      case 'profile':
        return <ProfileTab onNotice={setNotice} />
      case 'context':
        return <ContextTab onNotice={setNotice} />
      case 'memory':
        return <MemoryTab onNotice={setNotice} />
      case 'services':
        return <ServicesTab onOpenConnectors={() => onNavigate?.('connectors')} />
      case 'phone':
        return <PhoneTab onNotice={setNotice} />
      case 'pet':
        return <PetTab onNotice={setNotice} />
    }
  }

  return (
    <div className="settings-layout h-full">
      <aside className="settings-nav" aria-label="Settings sections">
        <div className="settings-nav-heading">
          <h1>{t('settings.title')}</h1>
        </div>
        {SETTINGS_GROUPS.map((group) => (
          <div key={group.label} className="settings-nav-group">
            <div className="settings-nav-label">{group.label}</div>
            {group.items.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`settings-nav-item ${tab === key ? 'is-active' : ''}`}
                aria-current={tab === key ? 'page' : undefined}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </div>
        ))}
      </aside>

      <div className="settings-content">
        <div className="settings-content-inner">
          <button
            type="button"
            className="settings-back"
            onClick={() => onNavigate?.('chat')}
          >
            <ArrowLeft size={15} />
            {t('settings.back')}
          </button>
          {notice && (
            <p className="inline-notice flex items-center gap-2" role="status">
              <CollieFace size={16} />
              <span>{notice}</span>
            </p>
          )}
          <div className="settings-page">
            <header className="settings-page-header">
              <h2>{TAB_COPY[tab].title}</h2>
              <p>{TAB_COPY[tab].description}</p>
            </header>
            <div className="settings-page-body">
              <TabErrorBoundary tab={tab}>{renderTab()}</TabErrorBoundary>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
