import { describe, expect, it, vi } from 'vitest'
import {
  sendTerminalAttachmentPath,
  supportedTerminalImage,
  supportedTerminalTextFile,
  terminalFileValidationError,
  terminalImageValidationError,
} from '../components/terminal-image-attachments'

const file = (type: string, size = 1) => ({ type, size } as File)

describe('terminal image attachment frontend boundary', () => {
  it('claims only one supported image and leaves text or mixed clipboard data to xterm', () => {
    expect(supportedTerminalImage([file('image/png')])).toMatchObject({ type: 'image/png' })
    expect(supportedTerminalImage([file('text/plain')])).toBeNull()
    expect(supportedTerminalImage([file('image/png'), file('text/plain')])).toBeNull()
  })

  it('does not claim an oversized image', () => {
    expect(supportedTerminalImage([file('image/png', 10 * 1024 * 1024 + 1)])).toBeNull()
  })

  it('claims one supported text file but rejects unsafe drop shapes', () => {
    const markdown = { type: 'text/markdown', name: 'notes.md', size: 1 } as File
    expect(supportedTerminalTextFile([markdown])).toBe(markdown)
    expect(terminalFileValidationError([{ type: 'text/plain', name: 'unsafe.exe', size: 1 } as File]))
      .toBe('Supported file types: PNG, JPEG, WebP, MD, TXT, JSON, YAML, CSV, LOG, and ZIP')
    expect(terminalFileValidationError([markdown, markdown])).toBe('Drop one supported file (ZIP up to 25 MiB)')
  })

  it.each(['.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.log'])(
    'accepts approved text extension %s regardless of browser MIME metadata',
    extension => {
      for (const type of ['text/markdown', 'text/plain', 'application/json', 'application/octet-stream', '']) {
        const candidate = { type, name: `notes${extension}`, size: 1 } as File
        expect(terminalFileValidationError([candidate])).toBeNull()
        expect(supportedTerminalTextFile([candidate])).toBe(candidate)
      }
    },
  )

  it.each(['application/zip', 'application/x-zip-compressed', 'application/octet-stream', ''])(
    'accepts an opaque ZIP regardless of browser MIME metadata: %s',
    type => {
      const archive = { type, name: 'bundle.zip', size: 1 } as File
      expect(terminalFileValidationError([archive])).toBeNull()
      expect(supportedTerminalTextFile([archive])).toBe(archive)
    },
  )

  it('continues to reject unsupported text files and oversized approved files', () => {
    expect(terminalFileValidationError([{ type: 'application/octet-stream', name: 'unsafe.exe', size: 1 } as File]))
      .toBe('Supported file types: PNG, JPEG, WebP, MD, TXT, JSON, YAML, CSV, LOG, and ZIP')
    expect(terminalFileValidationError([{ type: '', name: 'notes.md', size: 10 * 1024 * 1024 + 1 } as File]))
      .toBe('File must be 10 MiB or smaller')
    expect(terminalFileValidationError([{ type: '', name: 'bundle.zip', size: 10 * 1024 * 1024 + 1 } as File]))
      .toBeNull()
    expect(terminalFileValidationError([{ type: '', name: 'bundle.zip', size: 25 * 1024 * 1024 + 1 } as File]))
      .toBe('File must be 25 MiB or smaller')
  })

  it('gives a concise reason for unsupported and oversized image drops', () => {
    expect(terminalImageValidationError([file('image/gif')])).toBe('Only PNG, JPEG, and WebP images can be attached')
    expect(terminalImageValidationError([file('image/png', 10 * 1024 * 1024 + 1)])).toBe('Image must be 10 MiB or smaller')
  })

  it('inserts the returned absolute path as terminal input without adding Enter', () => {
    const send = vi.fn()
    const ws = { readyState: WebSocket.OPEN, send } as unknown as WebSocket

    sendTerminalAttachmentPath(ws, '/runtime/terminal-attachments/abcd1234/image.png')

    expect(send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: '/runtime/terminal-attachments/abcd1234/image.png' }),
    )
  })
})
