import { describe, expect, it } from 'vitest'
import type { ConnectorCatalogItem } from './ipc'
import { splitConnectorCatalog } from './connectorCatalog'

function connector(
  id: string,
  category: string,
  featured: boolean
): ConnectorCatalogItem {
  return {
    id,
    name: id,
    category,
    description: '',
    auth: 'oauth',
    driver: 'official_mcp',
    capabilities: [],
    permissions: [],
    featured,
    available: false,
    release_status: 'coming_soon',
    note: '',
    status: 'coming_soon',
    connection_count: 0
  }
}

describe('connection catalogue layout', () => {
  const items = [
    connector('notion', 'Notes & Tasks', true),
    connector('linear', 'Work', false)
  ]

  it('renders featured entries only once when browsing', () => {
    const result = splitConnectorCatalog(items, false)
    expect(result.featured.map((item) => item.id)).toEqual(['notion'])
    expect(result.categorized.map((item) => item.id)).toEqual(['linear'])
  })

  it('keeps every matching result in its category while searching', () => {
    const result = splitConnectorCatalog(items, true)
    expect(result.featured).toEqual([])
    expect(result.categorized.map((item) => item.id)).toEqual(['notion', 'linear'])
  })
})
