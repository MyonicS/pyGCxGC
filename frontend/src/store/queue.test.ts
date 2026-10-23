import { describe, expect, it } from 'vitest'

import { queueMaskEdit } from './session'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

describe('mask edit queue', () => {
  it('runs the edits of a document in order, after failures too', async () => {
    const log: string[] = []
    const a = queueMaskEdit('d', async () => {
      await sleep(30)
      log.push('a')
    })
    const b = queueMaskEdit('d', async () => {
      log.push('b')
      throw new Error('b failed')
    })
    const c = queueMaskEdit('d', async () => {
      log.push('c')
      return 3
    })
    // another document is not held up
    const other = queueMaskEdit('e', async () => log.push('other'))
    await expect(b).rejects.toThrow('b failed')
    expect(await c).toBe(3)
    await Promise.all([a, other])
    expect(log).toEqual(['other', 'a', 'b', 'c'])
  })
})
