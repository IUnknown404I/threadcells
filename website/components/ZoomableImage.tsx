'use client'

import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { X, ZoomIn } from '@/components/Icons'
import type { Locale } from '@/lib/locales'

type ZoomableImageProps = {
  src: string
  alt: string
  width: number
  height: number
  eager?: boolean
  className?: string
  locale?: Locale
}

const subscribeToBrowser = () => () => undefined

export function ZoomableImage({ src, alt, width, height, eager = false, className = '', locale = 'en' }: ZoomableImageProps) {
  const ru = locale === 'ru'
  const copy = ru ? {
    expanded: `Развёрнутый скриншот: ${alt}`,
    close: 'Закрыть развёрнутый скриншот',
    closeButton: 'Закрыть',
    hint: 'Нажмите на изображение, фон или Esc, чтобы закрыть.',
    expand: `Открыть: ${alt}`,
    expandButton: 'Открыть',
  } : {
    expanded: `Expanded screenshot: ${alt}`,
    close: 'Close expanded screenshot',
    closeButton: 'Close',
    hint: 'Click the image, the backdrop, or press Esc to close.',
    expand: `Click to expand: ${alt}`,
    expandButton: 'Click to expand',
  }
  const [open, setOpen] = useState(false)
  const mounted = useSyncExternalStore(subscribeToBrowser, () => true, () => false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previousOverflow = document.body.style.overflow
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : triggerRef.current
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        return
      }
      if (event.key !== 'Tab') return
      const dialog = closeRef.current?.closest('[role="dialog"]')
      const focusable = dialog?.querySelectorAll<HTMLElement>('button, a[href], [tabindex]:not([tabindex="-1"])')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      requestAnimationFrame(() => previousFocus?.focus())
    }
  }, [open])

  const modal = open && mounted ? createPortal(
    <div className="screenshot-lightbox" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }}>
      <div className="lightbox-dialog" role="dialog" aria-modal="true" aria-label={copy.expanded}>
        <button ref={closeRef} type="button" className="lightbox-close" onClick={() => setOpen(false)} aria-label={copy.close}><X size={20} /> {copy.closeButton}</button>
        <button type="button" className="lightbox-image-button" onClick={() => setOpen(false)} aria-label={copy.close}>
          <img src={src} alt={alt} width={width} height={height} />
        </button>
        <p>{copy.hint}</p>
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <>
      <button ref={triggerRef} type="button" className={`screenshot-trigger ${className}`} onClick={() => setOpen(true)} aria-label={copy.expand}>
        <img src={src} alt={alt} loading={eager ? 'eager' : 'lazy'} width={width} height={height} />
        <span className="screenshot-expand"><ZoomIn size={16} /> {copy.expandButton}</span>
      </button>
      {modal}
    </>
  )
}
