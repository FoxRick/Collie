import React from 'react'
import { createRoot } from 'react-dom/client'
import Sidebar from '../../src/renderer/src/components/Sidebar'
import TakeawayCard from '../../src/renderer/src/components/cards/TakeawayCard'
import ConnectorAddDialog from '../../src/renderer/src/components/connectors/ConnectorAddDialog'
import '../../src/renderer/src/styles/globals.css'

const noop = (): void => {}
createRoot(document.getElementById('root')!).render(
  <div className="app-shell flex h-full">
    <Sidebar conversations={[]} activeId={null} activeView="chat" onNavigate={noop}
      onSelect={noop} onNewChat={noop} onTemporaryChat={noop} onDelete={noop}
      onProjectChange={noop} onAddProject={noop} />
    <main className="workspace flex min-w-0 flex-1 flex-col">
      <TakeawayCard digest={{ points: [{ text: 'A readable recap in either theme.' }], chips: [{ key: 'Status', value: 'Ready' }] }} />
      <ConnectorAddDialog busy={false} onClose={noop} onChooseImport={async () => null}
        onDiscardImportSecrets={async () => {}} onSubmit={async () => {}}
        onValidate={async () => ({ valid: true, preview: null, warnings: [], errors: [] })} />
    </main>
  </div>
)
