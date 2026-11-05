import type { Severity } from '../types/domain'
export function StatusPill({children,severity='neutral'}:{children:React.ReactNode,severity?:Severity}){
  return <span className={`pill ${severity}`}>{children}</span>
}
