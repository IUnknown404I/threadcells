const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '')

const optionalUrl = (name: 'SITE_URL') => {
  const value = process.env[name]?.trim()
  if (!value) return null
  try {
    const url = new URL(value)
    url.hash = ''
    url.search = ''
    return url.protocol === 'https:' || url.hostname === 'localhost' ? trimTrailingSlash(url.toString()) : null
  } catch {
    return null
  }
}

const configuredBasePath = process.env.NEXT_PUBLIC_BASE_PATH?.trim() || ''
export const basePath = configuredBasePath === '/' ? '' : trimTrailingSlash(configuredBasePath)

export const assetPath = (path: string) => `${basePath}${path.startsWith('/') ? path : `/${path}`}`

export const publicRepositoryUrl = 'https://github.com/IUnknown404I/threadcells'
export const publicPagesUrl = 'https://iunknown404i.github.io/threadcells'

export const site = {
  name: 'ThreadCells',
  title: 'ThreadCells — Control plane for native CLI coding agents',
  description:
    'Run coding agents as a coordinated system. ThreadCells keeps workflows moving, protects durable history, and maintains its own orchestration environment on your Linux host.',
  siteUrl: optionalUrl('SITE_URL') || publicPagesUrl,
  githubUrl: publicRepositoryUrl,
  docsUrl: assetPath('/docs'),
  creator: {
    name: 'Subaev Ruslan',
    url: 'https://github.com/IUnknown404I',
  },
}

export const absoluteAssetUrl = (path: string) =>
  site.siteUrl ? `${site.siteUrl}${path.startsWith('/') ? path : `/${path}`}` : null

export const canonicalUrl = (path: string) =>
  site.siteUrl ? `${site.siteUrl}${path === '/' ? '' : path.startsWith('/') ? path : `/${path}`}` : null
