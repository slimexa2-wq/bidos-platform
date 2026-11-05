import { apiProvider } from './apiProvider'
import { mockProvider } from './mockProvider'
// 缺省连真接口；只有显式 VITE_DATA_MODE=mock 才启用内存假数据（防止换机即假数据站）。
export const provider = import.meta.env.VITE_DATA_MODE === 'mock' ? mockProvider : apiProvider
// 页面用的契约类型随 provider 一并出口（字段编辑 / 我方结果 / 候选动作）
export type { FieldEdit, FieldEditResponse, OwnResultRecord, CandidateActionResponse } from './dataProvider'
