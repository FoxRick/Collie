import type { ConnectorCatalogItem } from './ipc'

export function splitConnectorCatalog(
  items: ConnectorCatalogItem[],
  searching: boolean
): {
  featured: ConnectorCatalogItem[]
  categorized: ConnectorCatalogItem[]
  categories: string[]
} {
  const featured = searching ? [] : items.filter((item) => item.featured)
  const categorized = searching ? items : items.filter((item) => !item.featured)
  return {
    featured,
    categorized,
    categories: [...new Set(categorized.map((item) => item.category))]
  }
}
