import { Plug } from 'lucide-react'
import type { CSSProperties } from 'react'

import airtableLogo from '../assets/providers/airtable.svg'
import anthropicLogo from '../assets/providers/anthropic.svg'
import atlassianLogo from '../assets/providers/atlassian.svg'
import canvaLogo from '../assets/providers/canva.svg'
import deepseekLogo from '../assets/providers/deepseek.svg'
import discordLogo from '../assets/providers/discord.svg'
import dropboxLogo from '../assets/providers/dropbox.svg'
import figmaLogo from '../assets/providers/figma.svg'
import geminiLogo from '../assets/providers/gemini.svg'
import githubLogo from '../assets/providers/github.svg'
import gmailLogo from '../assets/providers/gmail.svg'
import googleCalendarLogo from '../assets/providers/google-calendar.svg'
import googleDocsLogo from '../assets/providers/google-docs.svg'
import googleDriveLogo from '../assets/providers/google-drive.svg'
import googleMeetLogo from '../assets/providers/google-meet.svg'
import googleSheetsLogo from '../assets/providers/google-sheets.svg'
import groqLogo from '../assets/providers/groq.svg'
import linearLogo from '../assets/providers/linear.svg'
import microsoftCopilotLogo from '../assets/providers/microsoft-copilot.svg'
import microsoftExcelLogo from '../assets/providers/microsoft-excel.svg'
import microsoftOneDriveLogo from '../assets/providers/microsoft-onedrive.svg'
import microsoftOutlookLogo from '../assets/providers/microsoft-outlook.svg'
import microsoftPowerPointLogo from '../assets/providers/microsoft-powerpoint.svg'
import microsoftTeamsLogo from '../assets/providers/microsoft-teams.svg'
import microsoftWordLogo from '../assets/providers/microsoft-word.svg'
import notionLogo from '../assets/providers/notion.svg'
import ollamaLogo from '../assets/providers/ollama.svg'
import openaiLogo from '../assets/providers/openai.svg'
import openrouterLogo from '../assets/providers/openrouter.svg'
import perplexityLogo from '../assets/providers/perplexity.svg'
import shopifyLogo from '../assets/providers/shopify.svg'
import slackLogo from '../assets/providers/slack.svg'
import telegramLogo from '../assets/providers/telegram.svg'
import todoistLogo from '../assets/providers/todoist.svg'
import trelloLogo from '../assets/providers/trello.svg'
import whatsappLogo from '../assets/providers/whatsapp.svg'
import zoomLogo from '../assets/providers/zoom.svg'

const LOGOS: Record<string, string> = {
  airtable: airtableLogo,
  anthropic: anthropicLogo,
  atlassian: atlassianLogo,
  canva: canvaLogo,
  chatgpt: openaiLogo,
  claude: anthropicLogo,
  copilot: microsoftCopilotLogo,
  deepseek: deepseekLogo,
  discord: discordLogo,
  dropbox: dropboxLogo,
  figma: figmaLogo,
  gemini: geminiLogo,
  google: geminiLogo,
  github: githubLogo,
  gmail: gmailLogo,
  'google-calendar': googleCalendarLogo,
  googlecalendar: googleCalendarLogo,
  'google-docs': googleDocsLogo,
  googledocs: googleDocsLogo,
  'google-drive': googleDriveLogo,
  googledrive: googleDriveLogo,
  'google-meet': googleMeetLogo,
  googlemeet: googleMeetLogo,
  'google-sheets': googleSheetsLogo,
  googlesheets: googleSheetsLogo,
  groq: groqLogo,
  linear: linearLogo,
  'microsoft-copilot': microsoftCopilotLogo,
  'microsoft-excel': microsoftExcelLogo,
  'microsoft-onedrive': microsoftOneDriveLogo,
  'microsoft-outlook': microsoftOutlookLogo,
  'microsoft-powerpoint': microsoftPowerPointLogo,
  'microsoft-teams': microsoftTeamsLogo,
  'microsoft-word': microsoftWordLogo,
  notion: notionLogo,
  ollama: ollamaLogo,
  openai: openaiLogo,
  openrouter: openrouterLogo,
  outlook: microsoftOutlookLogo,
  'outlook-calendar': microsoftOutlookLogo,
  'outlook-email': microsoftOutlookLogo,
  perplexity: perplexityLogo,
  shopify: shopifyLogo,
  slack: slackLogo,
  telegram: telegramLogo,
  todoist: todoistLogo,
  trello: trelloLogo,
  onedrive: microsoftOneDriveLogo,
  whatsapp: whatsappLogo,
  zoom: zoomLogo
}

function logoKey(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
}

export default function BrandLogo({
  brand,
  name = brand,
  size = 42,
  className = ''
}: {
  brand: string
  name?: string
  size?: number
  className?: string
}): React.JSX.Element {
  const source = LOGOS[logoKey(brand)]
  const fallback = name.trim().charAt(0).toUpperCase() || <Plug size={size / 2} />
  const style: CSSProperties = {
    width: size,
    height: size,
    borderColor: 'var(--collie-border)'
  }

  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-xl border bg-white ${className}`}
      style={style}
    >
      {source ? (
        <img src={source} alt="" style={{ width: size * 0.58, height: size * 0.58 }} />
      ) : (
        <span className="text-sm font-bold" style={{ color: 'var(--collie-btn-primary-bg)' }}>
          {fallback}
        </span>
      )}
    </span>
  )
}
