import { createInterface } from 'node:readline'

const PROTOCOL_VERSION = '2025-06-18'

function reply(id, result) {
  process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id, result })}\n`)
}

function fail(id, code, message) {
  process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } })}\n`)
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })

lines.on('line', (line) => {
  let request
  try {
    request = JSON.parse(line)
  } catch {
    return
  }

  if (request.method === 'initialize') {
    reply(request.id, {
      protocolVersion: request.params?.protocolVersion || PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: '@collie/mcp-probe-server', version: '0.1.0' }
    })
    return
  }

  if (request.method === 'notifications/initialized') return

  if (request.method === 'tools/list') {
    reply(request.id, {
      tools: [
        {
          name: 'collie_bundle_probe',
          description: 'Confirms that Collie can start and call a packaged MCP server.',
          inputSchema: { type: 'object', properties: {}, additionalProperties: false }
        }
      ]
    })
    return
  }

  if (request.method === 'tools/call' && request.params?.name === 'collie_bundle_probe') {
    reply(request.id, {
      content: [{ type: 'text', text: 'Collie packaged MCP runtime is ready.' }],
      isError: false
    })
    return
  }

  if (request.id !== undefined) fail(request.id, -32601, 'Method not found')
})
