import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, ShieldCheck } from 'lucide-react'
import { collieClient, type CollieEvent, type MessengerInfo } from '../../lib/ipc'
import BrandLogo from '../BrandLogo'

interface Props {
  onNotice: (msg: string) => void
}

export default function PhoneTab({ onNotice }: Props): React.JSX.Element {
  const [telegram, setTelegram] = useState<MessengerInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState('')

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const data = await collieClient.getMessengers()
      setTelegram(data.messengers.find((item) => item.id === 'telegram') ?? null)
    } catch {
      setTelegram(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    return collieClient.on((event: CollieEvent) => {
      if (
        (event.type === 'messenger_status' || event.type === 'messenger_pairing') &&
        event.messenger === 'telegram'
      ) {
        void refresh()
      }
    })
  }, [refresh])

  const connect = async (): Promise<void> => {
    const cleanToken = token.trim()
    if (!cleanToken) return
    setBusy(true)
    try {
      // The core asks Telegram to validate the token before it is saved.
      await collieClient.setMessengerSecret('telegram', 'token', cleanToken)
      const saved = await window.collie?.saveSecret('messenger:telegram:token', cleanToken)
      if (!saved) throw new Error("Windows couldn't encrypt the Telegram token.")
      const data = await collieClient.setMessenger('telegram', { enabled: true })
      setTelegram(data.messengers.find((item) => item.id === 'telegram') ?? null)
      setToken('')
      onNotice('Telegram is ready. Send your bot its first message to request pairing.')
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'Telegram did not accept that token.')
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async (): Promise<void> => {
    setBusy(true)
    try {
      await collieClient.setMessenger('telegram', { enabled: false })
      await window.collie?.deleteSecret('messenger:telegram:token')
      onNotice('Telegram disconnected. Your local bot token was removed.')
      await refresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not disconnect Telegram.')
    } finally {
      setBusy(false)
    }
  }

  const approve = async (code: string): Promise<void> => {
    try {
      const result = await collieClient.approvePairing(code)
      onNotice(
        result.confirmed
          ? 'Paired! I sent a test reply in Telegram.'
          : 'Paired, but the test reply did not get through. Check the connection.'
      )
      await refresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'That pairing request expired.')
    }
  }

  const deny = async (code: string): Promise<void> => {
    try {
      await collieClient.denyPairing(code)
      onNotice('Pairing request rejected.')
      await refresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'That pairing request expired.')
    }
  }

  const revoke = async (senderId: string): Promise<void> => {
    try {
      await collieClient.revokeMessengerSender('telegram', senderId)
      onNotice('That Telegram sender is no longer paired.')
      await refresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not revoke that sender.')
    }
  }

  if (loading) {
    return <p className="py-8 text-center text-sm">Checking Telegram…</p>
  }

  const connected = Boolean(telegram?.enabled && telegram.running && telegram.connected)
  const pending = telegram?.pending ?? []

  return (
    <div>
      <h2 className="mb-1 text-xl font-semibold">Telegram</h2>
      <p className="mb-4 text-sm" style={{ color: 'var(--collie-paw)' }}>
        Chat with Collie from Telegram. Your bot token is encrypted by Windows and never
        shown in chat or approvals.
      </p>

      <section className="rounded-xl border p-4" style={{ borderColor: 'var(--collie-fur)' }}>
        <div className="mb-4 flex items-start gap-3">
          <BrandLogo brand="telegram" size={36} />
          <div>
            <div className="font-medium">
              {connected ? 'Connected' : telegram?.enabled ? 'Needs attention' : 'Not connected'}
            </div>
            <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>
              {telegram?.error || 'Only approved Telegram accounts can talk to Collie.'}
            </div>
          </div>
        </div>

        {!telegram?.enabled ? (
          <div className="space-y-3 text-sm">
            <ol className="list-decimal space-y-2 pl-5">
              <li>
                Open{' '}
                <button
                  type="button"
                  className="inline-flex items-center gap-1 underline"
                  onClick={() => void window.collie?.openExternal('https://t.me/BotFather')}
                >
                  @BotFather <ExternalLink size={12} />
                </button>{' '}
                in Telegram.
              </li>
              <li>
                Send <code>/newbot</code> and follow BotFather’s naming prompts.
              </li>
              <li>Copy the bot token BotFather gives you and paste it below.</li>
            </ol>
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="123456789:AA…"
              aria-label="Telegram bot token"
              autoComplete="off"
              className="w-full rounded-lg border px-3 py-2 text-sm"
              style={{ borderColor: 'var(--collie-border)' }}
            />
            <button
              type="button"
              onClick={() => void connect()}
              disabled={busy || !token.trim()}
              className="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              style={{ background: 'var(--collie-btn-primary-bg)' }}
            >
              {busy ? 'Validating…' : 'Validate & connect'}
            </button>
          </div>
        ) : (
          <div className="space-y-3 text-sm">
            <p>
              Send your bot a message. The desktop will show the sender and a pairing
              request here.
            </p>
            {pending.map((request) => (
              <div
                key={request.code}
                className="flex flex-wrap items-center gap-2 rounded-lg border p-3"
                style={{ borderColor: 'var(--collie-border)' }}
              >
                <ShieldCheck size={16} />
                <span className="flex-1">
                  Sender <code>{request.sender_id}</code> wants to pair.
                </span>
                <button
                  type="button"
                  onClick={() => void approve(request.code)}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium text-white"
                  style={{ background: 'var(--collie-btn-primary-bg)' }}
                >
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => void deny(request.code)}
                  className="rounded-lg border px-3 py-1.5 text-xs font-medium"
                  style={{ borderColor: 'var(--collie-border)' }}
                >
                  Reject
                </button>
              </div>
            ))}

            {telegram.approved.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide">
                  Paired senders
                </div>
                {telegram.approved.map((sender) => (
                  <button
                    key={sender}
                    type="button"
                    onClick={() => void revoke(sender)}
                    className="mr-2 rounded-lg border px-3 py-1.5 text-xs"
                    style={{ borderColor: 'var(--collie-border)' }}
                  >
                    Revoke {sender}
                  </button>
                ))}
              </div>
            )}

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={telegram.deliver_automations}
                onChange={() =>
                  void collieClient
                    .setMessenger('telegram', {
                      deliver_automations: !telegram.deliver_automations
                    })
                    .then(() => refresh())
                }
              />
              Send routine results and reminders to Telegram
            </label>

            <button
              type="button"
              onClick={() => void disconnect()}
              disabled={busy}
              className="rounded-lg border px-3 py-1.5 text-xs font-medium"
              style={{ borderColor: 'var(--collie-border)' }}
            >
              Disconnect Telegram
            </button>
          </div>
        )}
      </section>
    </div>
  )
}
