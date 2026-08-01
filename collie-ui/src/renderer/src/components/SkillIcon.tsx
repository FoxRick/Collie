import {
  Bot,
  Braces,
  CalendarDays,
  ChartNoAxesColumnIncreasing,
  FileText,
  Globe2,
  Image,
  Mail,
  MessageCircle,
  Search,
  ShieldCheck,
  Shapes,
  Table2,
  TerminalSquare,
  Wrench
} from 'lucide-react'

const ICON_RULES = [
  { words: ['calendar', 'schedule', 'routine'], icon: CalendarDays, tone: 'sage' },
  { words: ['mail', 'email', 'gmail'], icon: Mail, tone: 'clay' },
  { words: ['sheet', 'excel', 'table', 'csv'], icon: Table2, tone: 'green' },
  { words: ['document', 'docs', 'word', 'pdf'], icon: FileText, tone: 'blue' },
  { words: ['image', 'photo', 'visual'], icon: Image, tone: 'amber' },
  { words: ['search', 'research', 'browser', 'web'], icon: Search, tone: 'blue' },
  { words: ['code', 'github', 'terminal', 'developer'], icon: TerminalSquare, tone: 'slate' },
  { words: ['data', 'chart', 'analytics'], icon: ChartNoAxesColumnIncreasing, tone: 'green' },
  { words: ['message', 'slack', 'telegram', 'chat'], icon: MessageCircle, tone: 'clay' },
  { words: ['security', 'safety'], icon: ShieldCheck, tone: 'sage' },
  { words: ['agent', 'bot'], icon: Bot, tone: 'slate' },
  { words: ['api', 'json'], icon: Braces, tone: 'blue' },
  { words: ['tool', 'mcp'], icon: Wrench, tone: 'amber' }
]

export default function SkillIcon({ name, size = 20 }: { name: string; size?: number }): React.JSX.Element {
  const normalized = name.toLowerCase()
  const match = ICON_RULES.find(({ words }) => words.some((word) => normalized.includes(word)))
  const Icon = match?.icon ?? (normalized.includes('skill') ? Shapes : Globe2)

  return (
    <span className={`skill-visual skill-visual--${match?.tone ?? 'sage'}`} aria-hidden="true">
      <Icon size={size} />
    </span>
  )
}
