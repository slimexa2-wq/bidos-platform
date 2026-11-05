# API Contract v0.2

前端仍通过 `DataProvider`，Mock 和真实 API 可切换。

## 业务读取

- `GET /api/dashboard`
- `GET /api/opportunities`
- `GET /api/registrations`
- `GET /api/evaluations`
- `GET /api/documents`
- `GET /api/results`
- `GET /api/competitors`
- `GET /api/alerts`

## 统一检索

- `GET /api/search?q=...&limit=20`

正式实现：结构化筛选 + trigram/FTS + pgvector + reranker。

## 数据源

- `POST /api/sources/sync`
- `GET /api/sources/health`

## AI 决策参考

- `GET /api/projects/{project_id}/decision-support?question=...`

返回 support / caution / unknown 三组证据，且 `human_decision_required=true`。

## 人工决策

- `POST /api/evaluations/{project_id}/human-decision`

## 人工反馈

- `POST /api/feedback`
- `GET /api/feedback`

## 审计

- `GET /api/audit`

## Source Pointer

- `GET /api/source-pointer/demo`（当前原型）
- 正式版：`GET /api/source-pointers/{id}`

## 后续写入接口

- `PATCH /api/opportunities/{id}`
- `POST /api/registrations/{id}/materials/generate`
- `PATCH /api/registrations/{id}/materials/{material_id}`
- `POST /api/documents/{id}/ai-generate`
- `POST /api/documents/{id}/quality-check`
- `POST /api/results/{id}/review`
- `POST /api/alerts/{id}/handle`

原则：UI 不直接访问 Excel、官网、向量数据库或模型供应商，统一经过 API 做权限、审计和来源追踪。
