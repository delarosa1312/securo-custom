// The running synthetic stack (fork/tools/stack.sh) and the three ways a scenario reaches it:
// the browser (Playwright page), REST (`rest`) and MCP (`mcp`). Same ports as stack.sh.
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'

const port = (name: string, fallback: number) => Number(process.env[name] ?? fallback)

export const stack = {
  frontend: `http://127.0.0.1:${port('FORK_STACK_FRONTEND_PORT', 13000)}`,
  backend: `http://127.0.0.1:${port('FORK_STACK_BACKEND_PORT', 18000)}`,
  mcp: `http://127.0.0.1:${port('FORK_STACK_MCP_PORT', 18765)}/mcp`,
}

// The seed user of fork/stack/seed.py (synthetic, local stack only).
export const seedUser = { email: 'e2e@example.com', password: 'Synthetic-E2E-2026' }

export const evidenceDir = path.resolve(process.env.FORK_E2E_OUT ?? 'results')

/** Save a JSON response or any value next to the screenshots. */
export function saveEvidence(name: string, value: unknown): void {
  mkdirSync(evidenceDir, { recursive: true })
  writeFileSync(path.join(evidenceDir, name), JSON.stringify(value, null, 2))
}

/** Viewport screenshot by default: a full-page capture resizes the page and restarts chart animations. */
export async function screenshot(page: Page, name: string, fullPage = false): Promise<void> {
  mkdirSync(evidenceDir, { recursive: true })
  await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage })
}

export async function restToken(user = seedUser): Promise<string> {
  const res = await fetch(`${stack.backend}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username: user.email, password: user.password }),
  })
  if (!res.ok) throw new Error(`login failed: ${res.status} ${await res.text()}`)
  return ((await res.json()) as { access_token: string }).access_token
}

export async function rest<T = unknown>(token: string, method: string, apiPath: string, body?: unknown): Promise<T> {
  const res = await fetch(`${stack.backend}${apiPath}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${method} ${apiPath} -> ${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

/** An external MCP client, as Claude Desktop would be: token minted by the app, JSON-RPC over HTTP. */
export async function mcpClient(restAuth: string) {
  const { token } = await rest<{ token: string }>(restAuth, 'POST', '/api/agents/mcp-tokens')
  let id = 0
  const rpc = async (method: string, params?: unknown) => {
    const res = await fetch(stack.mcp, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }),
    })
    const payload = (await res.json()) as { result?: any; error?: { message: string } }
    if (!res.ok || payload.error) throw new Error(`MCP ${method}: ${res.status} ${JSON.stringify(payload)}`)
    return payload.result
  }
  await rpc('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'fork-e2e', version: '0' } })
  return {
    /** Call a tool and return its structured result (the JSON text the tool returned). */
    async call(name: string, args: Record<string, unknown> = {}) {
      const result = await rpc('tools/call', { name, arguments: args })
      if (result?.isError) throw new Error(`MCP tool ${name} failed: ${JSON.stringify(result.content)}`)
      if (result?.structuredContent) return result.structuredContent
      const text = result?.content?.find((c: { type: string }) => c.type === 'text')?.text
      return text ? JSON.parse(text) : result
    },
  }
}

/** Log in through the real login screen and wait for the app shell. */
export async function loginInBrowser(page: Page, user = seedUser): Promise<void> {
  await page.goto('/login')
  await page.locator('#email').fill(user.email)
  await page.locator('#password').fill(user.password)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL((url) => !url.pathname.startsWith('/login'))
}
