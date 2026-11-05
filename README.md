# BidOS 招投标智能工作平台 · Architecture v0.3

> **接手 / 运维先看 [`HANDOVER.md`](./HANDOVER.md)** —— 运行端口、代码地图、资料库位置、
> 定时任务、已知故障模式、验证方法、待办清单。本文是架构说明，不是运维入口。

这版不是只补页面，而是把“页面背后怎么真的做出来”补齐。

BidOS 的定位不变：**不重做现有招投标工作流，不替代原表；系统负责监测、索引、提取、关联、提醒和证据整理，AI 提供参考，人做最终决定。**

## v0.3 当前能力


### v0.3 新增：项目到期持续提醒

- 结果复盘支持时间段筛选；
- 已中标、未中标项目均可维护项目结束日期；
- 结束前90天开始提醒；
- 提醒不会因为已读消失，直到发现新招标公告 / 确认续签 / 人工关闭；
- 仅由服务期限推算的日期标记为“预计”。

### v0.3 新增：投标保证金退回台账

- 独立保证金台账页面；
- 金额、缴纳、应退、实退、负责人、状态、证据链；
- 应退日前3天提醒；
- 逾期未退持续提醒；
- 只有实际到账确认后关闭。

### 1. 可落地的后端核心

- `SourcePointer`：原公告、原文件页码、源表行/记录的统一证据定位；
- `Connector`：不同官网/表/文件来源统一接入；
- `IngestionPipeline`：抓取 → 规范化 → 索引 → 审计；
- `EventEngine`：截止、到期、数据冲突等确定性事件；
- `HybridSearchService`：原型混合检索，正式版可替换为 SQL + pgvector + reranker；
- `DecisionSupportService`：支持/谨慎/未知三侧证据，强制人工决策；
- `FeedbackService`：保存 AI 原输出、人工修正、原因和证据；
- `AuditService`：重要写入和人工决策审计；
- `SourceHealth`：来源失败可见，不允许静默漏抓。

### 2. 真实基础设施骨架

- PostgreSQL 16 + pgvector；
- `pg_trgm` 中文名称模糊匹配；
- Redis + Celery Worker；
- Celery Beat 定时调度；
- n8n 仅做外围通知/跨系统编排；
- Docker Compose 分 `prototype / real / automation` 三种启动方式。

### 3. 文档/语义检索预留

- Docling 文档解析适配器；
- `document_chunks.embedding vector(1024)`；
- BGE-M3 / BGE-reranker-v2-m3 的接入位置已明确；
- 关键功能不依赖 LLM 才能运行。

### 4. 前端 DataProvider 升级

继续保留 mock 模式，同时增加：

- 来源健康；
- AI 决策参考；
- 人工反馈；
- 人工决策保存；
- 更完整的 Source Pointer；
- 深链接失败时的降级行为。

## 目录

```text
BidOS_Prototype_v0.3/
├─ apps/
│  ├─ web/                    React + TypeScript UI
│  ├─ api/                    FastAPI + BidOS Core
│  │  └─ app/
│  │     ├─ connectors/       数据源适配
│  │     ├─ ingestion/        采集/规范化流水线
│  │     ├─ documents/        Docling 适配
│  │     ├─ indexing/         检索抽象
│  │     ├─ ai/               模型抽象
│  │     └─ services/         事件/证据/反馈/审计
│  └─ worker/                 Celery 异步任务
├─ infra/
│  ├─ postgres/init/          pgvector 数据模型
│  └─ n8n/                    外围自动化边界
├─ config/                    来源配置示例
├─ docs/                      架构、落地矩阵、失败兜底等
└─ tests/backend/             核心规则单测
```

## 快速启动 UI + Mock API

```bash
docker compose up --build
```

打开：

- Web: `http://localhost:8080`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## 启动真实基础设施骨架

```bash
docker compose --profile real up --build
```

会额外启动：

- PostgreSQL/pgvector
- Redis
- Worker
- Scheduler

目前业务读取默认仍走 Mock Repository，这样在真实数据接入前 UI 不会被阻塞。下一步按 `docs/REAL_DATA_INTEGRATION.md` 替换 Connector/Repository 即可。

## 启动 n8n（可选）

```bash
docker compose --profile automation up
```

n8n **不保存核心业务状态**，只做通知和外围编排。

## 后端本地运行

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 测试

```bash
python -m unittest discover -s tests/backend -v
```

## 真数据接入时的硬原则

1. 原表/原官网/原文件继续存在，BidOS 不制造第二套真相。
2. 关键 AI 结论尽量带 Source Pointer。
3. 自动写回前必须定义冲突规则和人工确认条件。
4. 网站抓取失败必须显式告警。
5. 项目到期、截止、保证金等优先用规则，不用 LLM 算日历。
6. AI 只能提供检索、归纳、参考和证据；参与、报价、风险接受和正式提交由人决定。

## 定版前先看

- `docs/FEATURE_IMPLEMENTATION_MATRIX.md`：每项功能到底怎么落地；
- `docs/FAILURE_MODES.md`：系统失败时怎么办；
- `docs/ARCHITECTURE.md`：全局架构；
- `docs/DATA_MODEL.md`：项目、实体、证据、事件、反馈如何关联；
- `docs/REAL_DATA_INTEGRATION.md`：真实数据嫁接顺序。
