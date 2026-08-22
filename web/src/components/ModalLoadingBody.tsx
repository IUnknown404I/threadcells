import { Loader2 } from 'lucide-react'

export function ModalLoadingBody({
  label = 'Loading',
  className = '',
}: {
  label?: string
  className?: string
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="modal-loading-body"
      className={`flex min-h-48 min-w-0 w-full flex-1 items-center justify-center self-stretch ${className}`}
    >
      <Loader2 size={24} aria-hidden="true" className="animate-spin text-gray-400" />
      <span className="sr-only">{label}</span>
    </div>
  )
}
