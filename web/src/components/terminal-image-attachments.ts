export const TERMINAL_IMAGE_MIME_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp'])
export const TERMINAL_IMAGE_MAX_BYTES = 10 * 1024 * 1024
export const TERMINAL_ARCHIVE_MAX_BYTES = 25 * 1024 * 1024
export const TERMINAL_TEXT_EXTENSIONS = new Set(['.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.log'])
export const TERMINAL_OPAQUE_FILE_EXTENSIONS = new Set(['.zip'])

/**
 * Browser clipboard implementations do not consistently populate DataTransfer.files
 * for images. Windows browsers commonly expose the image only as a file item.
 */
export function terminalClipboardFiles(clipboardData: DataTransfer | null): File[] {
  if (!clipboardData) return []

  const files = Array.from(clipboardData.files)
  if (files.length > 0) return files

  return Array.from(clipboardData.items)
    .filter(item => item.kind === 'file')
    .map(item => item.getAsFile())
    .filter((file): file is File => file !== null)
}

export function terminalImageValidationError(files: FileList | File[]): string | null {
  if (files.length !== 1) return 'Drop one PNG, JPEG, or WebP image (up to 10 MiB)'
  const [file] = Array.from(files)
  if (!TERMINAL_IMAGE_MIME_TYPES.has(file.type)) {
    return 'Only PNG, JPEG, and WebP images can be attached'
  }
  if (file.size > TERMINAL_IMAGE_MAX_BYTES) return 'Image must be 10 MiB or smaller'
  return null
}

export function supportedTerminalImage(files: FileList | File[]): File | null {
  if (terminalImageValidationError(files)) return null
  const [file] = Array.from(files)
  return file
}

export function terminalFileValidationError(files: FileList | File[]): string | null {
  if (files.length !== 1) return 'Drop one supported file (ZIP up to 25 MiB)'
  const [file] = Array.from(files)
  const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  const maxBytes = TERMINAL_IMAGE_MIME_TYPES.has(file.type) || !TERMINAL_OPAQUE_FILE_EXTENSIONS.has(extension)
    ? TERMINAL_IMAGE_MAX_BYTES
    : TERMINAL_ARCHIVE_MAX_BYTES
  const maxMiB = maxBytes / (1024 * 1024)
  if (file.size > maxBytes) return `File must be ${maxMiB} MiB or smaller`
  if (TERMINAL_IMAGE_MIME_TYPES.has(file.type)) return null
  if (!TERMINAL_TEXT_EXTENSIONS.has(extension) && !TERMINAL_OPAQUE_FILE_EXTENSIONS.has(extension)) {
    return 'Supported file types: PNG, JPEG, WebP, MD, TXT, JSON, YAML, CSV, LOG, and ZIP'
  }
  return null
}

export function supportedTerminalTextFile(files: FileList | File[]): File | null {
  if (terminalFileValidationError(files)) return null
  const [file] = Array.from(files)
  return TERMINAL_IMAGE_MIME_TYPES.has(file.type) ? null : file
}

export function sendTerminalAttachmentPath(ws: WebSocket, path: string): void {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'input', data: path }))
  }
}
