import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createGzip } from 'node:zlib'

const websiteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const outRoot = path.join(websiteRoot, 'out')

const types = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.mp4': 'video/mp4',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webm': 'video/webm',
  '.webp': 'image/webp',
  '.xml': 'application/xml; charset=utf-8',
  '.woff2': 'font/woff2',
}

const existingFile = async (candidate) => {
  try {
    return (await stat(candidate)).isFile() ? candidate : null
  } catch {
    return null
  }
}

export async function startStaticServer({ basePath = '' } = {}) {
  const normalizedBase = basePath === '/' ? '' : basePath.replace(/\/$/, '')
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://localhost')
    if (normalizedBase && !(url.pathname === normalizedBase || url.pathname.startsWith(`${normalizedBase}/`))) {
      response.writeHead(404).end('Not found')
      return
    }
    const relativeUrl = decodeURIComponent(normalizedBase ? url.pathname.slice(normalizedBase.length) || '/' : url.pathname)
    if (relativeUrl.includes('\0') || relativeUrl.split('/').includes('..')) {
      response.writeHead(400).end('Bad request')
      return
    }
    const requested = path.join(outRoot, relativeUrl)
    const file = await existingFile(requested)
      || await existingFile(path.join(requested, 'index.html'))
      || await existingFile(`${requested}.html`)
      || await existingFile(path.join(outRoot, '404.html'))
    if (!file) {
      response.writeHead(404).end('Not found')
      return
    }
    const extension = path.extname(file).toLowerCase()
    const contentType = types[extension] || 'application/octet-stream'
    const compress = /^(text\/|application\/(javascript|json|xml))/.test(contentType)
      && request.headers['accept-encoding']?.includes('gzip')
    response.writeHead(file.endsWith('404.html') ? 404 : 200, {
      'content-type': contentType,
      'cache-control': 'public, max-age=300',
      ...(compress ? { 'content-encoding': 'gzip', vary: 'accept-encoding' } : {}),
    })
    const stream = createReadStream(file)
    if (compress) stream.pipe(createGzip()).pipe(response)
    else stream.pipe(response)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Static server did not bind')
  return {
    origin: `http://127.0.0.1:${address.port}${normalizedBase}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  }
}
