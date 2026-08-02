import { ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { collieClient, type ApprovalRule } from '../../lib/ipc'

export default function SafetyApprovalsTab(): React.JSX.Element {
  const [rules, setRules] = useState<ApprovalRule[]>([])
  const [preset, setPreset] = useState<'ask' | 'allow'>('ask')

  const refresh = (): void => {
    void collieClient
      .listApprovalRules()
      .then((data) => setRules(data.rules))
      .catch(() => setRules([]))
  }

  // Load the real preset from the core on mount — the select must not lie.
  const refreshPreset = (): void => {
    void collieClient
      .getSettings()
      .then(({ settings }) => {
        const stored = settings['permissions.local_write_preset']
        if (stored === 'allow' || stored === 'ask') setPreset(stored)
      })
      .catch(() => undefined)
  }

  useEffect(() => {
    refresh()
    refreshPreset()
  }, [])

  return (
    <div>
      <h2 className="mb-2 text-xl font-semibold">Safety & approvals</h2>
      <p className="settings-lead">
        Everyday help can stay smooth without giving Collie authority over consequential actions.
      </p>
      <section className="settings-card">
        <h3>Local changes</h3>
        <label className="form-field">
          <span>Approvals</span>
          <select
            value={preset}
            onChange={(event) => {
              const value = event.target.value as 'ask' | 'allow'
              setPreset(value)
              collieClient
                .setApprovalPreset(value)
                .catch(() => {
                  // The change did not reach the core — show the real value.
                  refreshPreset()
                })
            }}
          >
            <option value="ask">Ask me</option>
            <option value="allow">Approve for me</option>
          </select>
          <small>
            {preset === 'allow'
              ? 'Eligible ordinary local actions can continue. Consequential actions still ask.'
              : 'Collie asks before bounded file edits and other eligible local changes that still need approval.'}
          </small>
        </label>
      </section>
      <section className="settings-card">
        <h3><ShieldCheck size={16} /> Saved approval rules</h3>
        {rules.length === 0 ? <p>No saved rules yet.</p> : (
          <div className="approval-rule-list">
            {rules.map((rule) => (
              <div key={rule.id}>
                <span><strong>{rule.action}</strong><small>{rule.scope_type} · {rule.resource_pattern}</small></span>
                <button
                  className="icon-button"
                  aria-label={`Remove ${rule.action} rule`}
                  onClick={() =>
                    void collieClient
                      .deleteApprovalRule(rule.id)
                      .then(refresh)
                      .catch(() => undefined)
                  }
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
