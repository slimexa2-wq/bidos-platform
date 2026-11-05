import type { Opportunity } from '../types/domain'
import { StatusPill } from './StatusPill'
import { formatBudgetWan } from '../utils/format'
export function ProjectTable({items,selected,onSelect}:{items:Opportunity[],selected?:string,onSelect:(id:string)=>void}){
 return <div className="table-wrap"><table><thead><tr><th>项目名称</th><th>甲方</th><th>地区</th><th>预算(万)</th><th>截止</th><th>状态</th></tr></thead><tbody>{items.map(p=><tr key={p.id} className={selected===p.id?'selected':''} onClick={()=>onSelect(p.id)}><td><b>{p.name}</b><small>{p.projectType}</small></td><td>{p.client}</td><td>{p.region}</td><td>{formatBudgetWan(p.budget)}</td><td>{p.deadline.split(' ')[0]}</td><td><StatusPill severity={p.severity}>{p.status}</StatusPill></td></tr>)}</tbody></table></div>
}
