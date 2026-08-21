declare const __THREADCELLS_REVISION__: string
declare const __THREADCELLS_VERSION__: string

const revision = __THREADCELLS_REVISION__ === 'source'
  ? 'local source (revision not embedded)'
  : __THREADCELLS_REVISION__

export const BUILD_IDENTITY = {
  revision,
  version: __THREADCELLS_VERSION__,
} as const
