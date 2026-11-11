import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// Both portfolio and blind-bid drawers use the same keyboard and focus rules.
export function DialogSurface({ label, children, onClose, className, backdropClassName }: { label: string; children: ReactNode; onClose: () => void; className: string; backdropClassName: string }) {
  const backdrop = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const background = Array.from(document.body.children).filter((node): node is HTMLElement => node instanceof HTMLElement && node !== backdrop.current)
    const priorInert = background.map(node => node.inert)
    const priorOverflow = document.body.style.overflow
    const controls = () => Array.from(dialog.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])') || []).filter(node => node.getClientRects().length > 0)
    const focusFirst = () => (controls()[0] || dialog.current)?.focus()
    background.forEach(node => { node.inert = true })
    document.body.style.overflow = 'hidden'
    focusFirst()
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close.current(); return }
      if (event.key !== 'Tab') return
      const items = controls()
      const first = items[0], last = items[items.length - 1]
      if (!first) { event.preventDefault(); dialog.current?.focus(); return }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.current?.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    const focus = (event: FocusEvent) => { if (!dialog.current?.contains(event.target as Node)) focusFirst() }
    document.addEventListener('keydown', key, true)
    document.addEventListener('focusin', focus)
    return () => {
      document.removeEventListener('keydown', key, true)
      document.removeEventListener('focusin', focus)
      background.forEach((node, index) => { node.inert = priorInert[index] })
      document.body.style.overflow = priorOverflow
      if (opener?.isConnected) opener.focus()
    }
  }, [])
  return createPortal(<div ref={backdrop} className={backdropClassName} onClick={event => { if (event.target === event.currentTarget) close.current() }}><aside ref={dialog} className={className} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}>{children}</aside></div>, document.body)
}
