import type { NextConfig } from 'next'

const configuredBasePath = process.env.NEXT_PUBLIC_BASE_PATH?.trim() || ''
const basePath = configuredBasePath === '/' ? '' : configuredBasePath.replace(/\/$/, '')

const nextConfig: NextConfig = {
  output: 'export',
  trailingSlash: true,
  basePath,
  images: { unoptimized: true },
  reactStrictMode: true,
}

export default nextConfig
