import { Bot, Brain, MessagesSquare } from 'lucide-react'

export default function StatusCard({ data }: { data: Record<string, unknown> }): React.JSX.Element {
  return (
    <section className="status-card">
      <div><Brain size={16} /><span><small>Model</small><b>{String(data.model || 'Not connected')}</b></span></div>
      <div><MessagesSquare size={16} /><span><small>Session</small><b>{Number(data.messages || 0)} messages</b></span></div>
      <div><Bot size={16} /><span><small>Working</small><b>{Number(data.active_agents || 0)} agents</b></span></div>
    </section>
  )
}
