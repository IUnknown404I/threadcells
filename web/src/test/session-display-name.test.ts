import { describe, expect, it } from 'vitest'
import { sessionDisplayName } from '../sessionDisplayName'

describe('sessionDisplayName', () => {
  it('removes exactly one leading system prefix', () => {
    expect(sessionDisplayName('cao-session')).toBe('session')
    expect(sessionDisplayName('cao-cao-session')).toBe('cao-session')
    expect(sessionDisplayName('my-cao-session')).toBe('my-cao-session')
    expect(sessionDisplayName('CAO-session')).toBe('CAO-session')
  })
})
