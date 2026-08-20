/**
 * Desktop pet settings (F070, F078).
 *
 * The desktop Border Collie is not shipping in this release — the feature is
 * built and tested but gated off (PET_AVAILABLE in src/main/pet.ts). This tab
 * shows a "coming soon" card instead of live controls, so users know the
 * companion is on the way. Flip the gate and restore the previous controls
 * when it ships.
 */
interface Props {
  /** Shared settings-tab notice surface; unused while the pet is gated off. */
  onNotice: (msg: string) => void
}

export default function PetTab({ onNotice }: Props): React.JSX.Element {
  void onNotice
  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="font-semibold">Desktop Pet</h3>
        <span className="rounded-full px-2 py-0.5 text-xs font-medium" style={{ background: 'var(--collie-amber)', color: '#fff' }}>
          Coming soon
        </span>
      </div>

      <p className="mb-4 text-sm" style={{ color: 'var(--collie-paw)' }}>
        Your Border Collie is getting ready to move onto your desktop — with moods and
        reactions of her own. She&apos;s not here yet, but she&apos;s on the way.
      </p>

      <div className="rounded-lg border p-4" style={{ borderColor: 'var(--collie-paw)', background: 'var(--collie-card)' }}>
        <p className="text-sm">
          🐾 When she arrives, you&apos;ll be able to switch her on here, choose how she
          behaves, and keep her company while Collie works.
        </p>
      </div>
    </div>
  )
}
