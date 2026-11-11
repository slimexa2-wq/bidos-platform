import { useCallback, useEffect, useRef, useState } from 'react'

// Session storage keeps project drafts across route changes and reloads. The
// in-memory copy also protects navigation when browser storage is unavailable.
const drafts = new Map<string, unknown>()
const revisions = new Map<string, number>()
const listeners = new Map<string, Set<(value: unknown) => void>>()
const publish = (key: string, value: unknown) => listeners.get(key)?.forEach(listener => listener(value))
const unpersisted = new Set<string>()
const protectUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
function persist(key: string, value: { dirty: boolean }) {
  try {
    if (value.dirty) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
    unpersisted.delete(key)
  } catch {
    if (value.dirty) unpersisted.add(key)
    else unpersisted.delete(key)
  }
  // Failed-storage drafts outlive their editor route. Protect the document
  // until every in-memory-only draft is saved or explicitly discarded.
  window.removeEventListener('beforeunload', protectUnload)
  if (unpersisted.size) window.addEventListener('beforeunload', protectUnload)
}
export function useProjectDraft<T extends { dirty: boolean }>(scope: string, initial: T) {
  const key = `bidos:project-draft:v1:${scope}`
  const [value, setValue] = useState<T>(() => {
    const cached = drafts.get(key) as T | undefined
    if (cached) return cached
    try {
      const stored = sessionStorage.getItem(key)
      if (stored) return JSON.parse(stored) as T
    } catch { /* Draft writes track whether unload protection is needed. */ }
    return (drafts.get(key) as T | undefined) || initial
  })
  const current = useRef(value)
  useEffect(() => {
    const receive = (next: unknown) => { current.current = next as T; setValue(next as T) }
    const subscribers = listeners.get(key) || new Set<(value: unknown) => void>()
    subscribers.add(receive)
    listeners.set(key, subscribers)
    return () => { subscribers.delete(receive); if (!subscribers.size) listeners.delete(key) }
  }, [key])
  const getRevision = useCallback(() => revisions.get(key) || 0, [key])
  const update = useCallback((patch: Partial<T> | ((previous: T) => T), expectedRevision?: number) => {
    if (expectedRevision !== undefined && expectedRevision !== (revisions.get(key) || 0)) return false
    const previous = (drafts.get(key) as T | undefined) || current.current
    const next = typeof patch === 'function' ? patch(previous) : { ...previous, ...patch }
    current.current = next
    revisions.set(key, (revisions.get(key) || 0) + 1)
    drafts.set(key, next)
    persist(key, next)
    publish(key, next)
    return true
  }, [key])
  const reset = useCallback((next: T, expectedRevision?: number) => {
    // A request may finish after this editor unmounts and another instance edits
    // the same project. Only replace the draft that request actually saved.
    if (expectedRevision !== undefined && expectedRevision !== (revisions.get(key) || 0)) return false
    current.current = next
    revisions.set(key, (revisions.get(key) || 0) + 1)
    drafts.set(key, next)
    persist(key, next)
    publish(key, next)
    return true
  }, [key])
  return { value, update, reset, getRevision }
}
