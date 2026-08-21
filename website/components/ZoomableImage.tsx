'use client'

import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { X, ZoomIn } from '@/components/Icons'

type ZoomableImageProps = {
  src: string
  alt: string
  width: number
  height: number
  eager?: boolean
  className?: string
}

const subscribeToBrowser = () => () => undefined

export function ZoomableImage({ src, alt, width, height, eager = false, className = '' }: ZoomableImageProps) {
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
      <div className="lightbox-dialog" role="dialog" aria-modal="true" aria-label={`Expanded screenshot: ${alt}`}>
        <button ref={closeRef} type="button" className="lightbox-close" onClick={() => setOpen(false)} aria-label="Close expanded screenshot"><X size={20} /> Close</button>
        <button type="button" className="lightbox-image-button" onClick={() => setOpen(false)} aria-label="Close expanded screenshot">
          <img src={src} alt={alt} width={width} height={height} />
        </button>
        <p>Click the image, the backdrop, or press Esc to close.</p>
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <>
      <button ref={triggerRef} type="button" className={`screenshot-trigger ${className}`} onClick={() => setOpen(true)} aria-label={`Click to expand: ${alt}`}>
        <img src={src} alt={alt} loading={eager ? 'eager' : 'lazy'} width={width} height={height} />
        <span className="screenshot-expand"><ZoomIn size={16} /> Click to expand</span>
      </button>
      {modal}
    </>
  )
}
