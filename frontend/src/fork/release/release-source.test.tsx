import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '@/test/utils'
import { useLatestRelease } from '@/hooks/use-latest-release'
import { isUpdateAvailable } from '@/lib/semver'
import { LATEST_RELEASE_URL } from './release-source'

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('update check in the fork', () => {
  it("asks this fork's latest release, not upstream's", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          tag_name: 'v0.22.0-custom.2',
          html_url: 'https://github.com/delarosa1312/securo-custom/releases/tag/v0.22.0-custom.2',
        }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useLatestRelease(), { wrapper })

    await waitFor(() => expect(result.current.data).toBeTruthy())
    expect(LATEST_RELEASE_URL).toBe('https://api.github.com/repos/delarosa1312/securo-custom/releases/latest')
    expect(fetchMock).toHaveBeenCalledWith(LATEST_RELEASE_URL, expect.anything())
    expect(result.current.data?.tagName).toBe('v0.22.0-custom.2')
  })

  it('orders custom releases by upstream base, then by custom number', () => {
    expect(isUpdateAvailable('v0.22.0-custom.1', 'v0.22.0-custom.2')).toBe(true)
    expect(isUpdateAvailable('v0.22.0-custom.2', 'v0.22.0-custom.10')).toBe(true)
    expect(isUpdateAvailable('v0.22.0-custom.3', 'v0.23.0-custom.1')).toBe(true)
    expect(isUpdateAvailable('v0.22.0-custom.2', 'v0.22.0-custom.2')).toBe(false)
    expect(isUpdateAvailable('v0.22.0-custom.2', 'v0.22.0-custom.1')).toBe(false)
    expect(isUpdateAvailable('v0.23.0-custom.1', 'v0.22.0-custom.9')).toBe(false)
  })
})
