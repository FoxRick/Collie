import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../lib/ipc', () => ({
  collieClient: {
    listConnectorCatalog: vi.fn(),
    listConnectorConnections: vi.fn(),
    on: vi.fn(() => () => undefined),
    beginConnectorAuth: vi.fn(),
    cancelConnectorAuth: vi.fn(),
    updateConnector: vi.fn(),
    testConnector: vi.fn(),
    removeConnector: vi.fn()
  }
}))

import ConnectorCard from '../components/connectors/ConnectorCard'
import {
  connectorConnectNotice,
  connectorRemovalNotice,
  matchesActiveConnectorAuthStart,
  matchesActiveConnectorStatus
} from './ConnectorsScreen'

const connector = (status: string) => ({
  id: 'notion',
  name: 'Notion',
  category: 'Notes & Tasks',
  description: 'Find notes.',
  auth: 'oauth' as const,
  driver: 'official_mcp' as const,
  capabilities: ['Read'],
  permissions: ['read pages'],
  featured: true,
  available: true,
  release_status: 'alpha' as const,
  note: '',
  status,
  connection_count: 0
})

describe('connector connection status truthfulness', () => {
  it.each([
    ['authorizing', 'Sign-in in progress'],
    ['testing', 'Checking connection']
  ])('disables %s catalog cards while the connection is in flight', (status, label) => {
    const markup = renderToStaticMarkup(
      createElement(ConnectorCard, { connector: connector(status), onConnect: vi.fn() })
    )

    expect(markup).toContain(label)
    expect(markup).toContain('disabled=""')
    expect(markup).not.toContain('Not connected yet.')
  })

  it('only presents a successful message for an actually connected status', () => {
    expect(connectorConnectNotice('Notion', 'connected')).toContain('Connected to Notion')
    expect(connectorConnectNotice('Notion', 'authorizing')).toContain('already in progress')
    expect(connectorConnectNotice('Notion', 'attention')).toContain('needs attention')
    expect(connectorConnectNotice('Notion', 'auth_required')).toContain('fresh sign-in')
    expect(connectorConnectNotice('Notion', 'auth_required')).not.toContain('Connected')
    expect(connectorConnectNotice('Notion', 'failed')).not.toContain('Connected')
  })

  it('reports the provider-side result honestly after local removal', () => {
    expect(connectorRemovalNotice('Notion', 'revoked')).toContain('access was revoked')
    expect(connectorRemovalNotice('Notion', 'unsupported')).toContain("can't revoke access")
    expect(connectorRemovalNotice('Notion', 'failed')).toContain("couldn't confirm")
    expect(connectorRemovalNotice('Notion', 'not_applicable')).toContain('Connection removed')
  })

  it('only accepts auth and testing events for its active UI connection', () => {
    const active = { providerId: 'notion' }
    expect(
      matchesActiveConnectorAuthStart(true, active, {
        provider_id: 'notion',
        connection_id: 'con_screen',
        origin: 'connectors_ui'
      })
    ).toBe(true)
    expect(
      matchesActiveConnectorAuthStart(true, active, {
        provider_id: 'notion',
        connection_id: 'con_background',
        origin: 'chat'
      })
    ).toBe(false)
    expect(
      matchesActiveConnectorStatus('con_screen', {
        connection_id: 'con_background',
        status: 'testing'
      })
    ).toBe(false)
    expect(
      matchesActiveConnectorStatus('con_screen', {
        connection_id: 'con_screen',
        status: 'testing'
      })
    ).toBe(true)
  })
})
