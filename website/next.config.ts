import type { NextConfig } from 'next'
import { resolve } from 'node:path'

const configuredBasePath = process.env.NEXT_PUBLIC_BASE_PATH?.trim() || ''
const basePath = configuredBasePath === '/' ? '' : configuredBasePath.replace(/\/$/, '')

const nextConfig: NextConfig = {
  output: 'export',
  trailingSlash: true,
  basePath,
  images: { unoptimized: true },
  reactStrictMode: true,
  // The public site and authenticated app compile the same locale selector
  // source from the repository-level Web tree.
  turbopack: { root: resolve(__dirname, '..') },
}

export default nextConfig
