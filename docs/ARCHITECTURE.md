# BidOS 架构说明 v0.2

## 0. 设计目标

BidOS 不替代现有招投标流程和原表，而是给现有流程加一层：

- 自动监控外部来源；
- 自动提取、结构化、回填和关联；
- 通过统一索引让历史数据真正可查、可调、可复用；
- 用事件引擎把人的注意力集中到异常、临期、优质项目和结果变化；
- AI 负责检索、归纳、证据整理和参考思路；
- 人负责最终参与、报价、风险接受和正式提交。

## 1. 总体架构

```text
PC / Mobile Web
      ↓
React UI + DataProvider
      ↓
FastAPI 业务 API
      ↓
┌─────────────────────────────────────────────┐
│ 业务服务                                    │
│ 项目/报名/评估/标书/结果/竞对/权限/审计      │
└───────────────┬─────────────────────────────┘
                ↓
┌─────────────────────────────────────────────┐
│ BidOS Core                                  │
│ connectors → ingestion → normalize          │
│           → entity link → index             │
│           → event engine → AI/evidence      │
│           → source pointer → feedback       │
└───────┬──────────────┬───────────────┬──────┘
        ↓              ↓               ↓
 PostgreSQL/pgvector   Redis/Celery     n8n(外围)
        ↓
 原表/原文件/官网/第三方来源
```

## 2. 为什么核心不是“训练一个大模型”

系统要快、准、可核验，先依赖数据工程：

1. 结构化字段索引；
2. 中文全文/模糊检索；
3. 语义向量索引；
4. reranker 精排；
5. 实体统一（甲方、竞对别名）；
6. Source Pointer 回到原始证据；
7. 人工反馈闭环。

LLM 只读取已经筛过的少量证据，不临时翻完所有文件。

## 3. Source Pointer

任何重要 AI 结论都尽量绑定 Source Pointer。

```text
source_type       official | sheet | file | third_party | internal
source_name       来源名称
source_url        原始 URL
deep_link         若平台支持，直接跳具体记录/页码
project_id        关联项目
entity_id         关联甲方/竞对
file_id/file_name 文件来源
sheet_name        工作表
row_id/cell_range 表格位置
page_number       PDF 页码
section           条款/标题
block_id          在线文档块
record_id         在线表记录
original_text     命中的原文片段
content_hash      防重复/变化检测
trust_level       official | internal | reference
```

### UI 规则

重要结论的默认呈现顺序：

`结论 → 关键事实 → 来源 → 原文/源表跳转 → 人工操作`

## 4. 数据真相源

- 原始业务表、正式文件、官方公告可以继续是业务层 Source of Truth；
- BidOS PostgreSQL 保存统一 ID、索引、关联、事件、审计和反馈；
- 不要求把所有原始文件复制进 BidOS；
- 对关键公告可保留快照/Hash，防止源站后续修改或失效。

## 5. 采集与回退策略

每个来源按以下顺序尝试：

`官方 API/RSS → HTTP → Playwright → Browser Agent → 人工兜底`

每个来源必须记录健康状态：

- 最后成功时间；
- 最后检查时间；
- 连续失败次数；
- 本次发现数量；
- 是否需要登录；
- 是否需要人工处理；
- 最近错误。

系统绝不隐藏抓取失败。

## 6. 索引层

### PostgreSQL

保存项目、甲方、竞对、事件、Source Pointer、人工决策、反馈、审计。

### pg_trgm

用于中文项目名、甲方名、别名的模糊匹配。

### pgvector

`document_chunks.embedding vector(1024)` 预留 BGE-M3 向量。

### 推荐搜索链路

```text
结构化过滤
+ trigram/全文候选
+ BGE-M3 语义召回
        ↓
候选 Top N
        ↓
BGE-reranker-v2-m3
        ↓
Top 5-20 证据
        ↓
LLM 归纳
```

## 7. 事件引擎

优先使用确定性规则，而不是让 LLM 算日历。

核心事件：

- `NEW_OPPORTUNITY`
- `ANNOUNCEMENT_CHANGED`
- `DEADLINE_APPROACHING`
- `AWARD_PUBLISHED`
- `PROJECT_EXPIRING`
- `DEPOSIT_OVERDUE`
- `DATA_CONFLICT`
- `SOURCE_FAILED`
- `HUMAN_REVIEW_REQUIRED`

首页只展示高价值事件；提醒中心保存完整历史。

## 8. AI 决策参考

AI 不输出不可质疑的“最终答案”。

输入：

- 当前招标文件；
- 公司资质和历史项目；
- 历史参与/未参与结果；
- 相似项目；
- 甲方历史；
- 竞对参与/报价/中标历史；
- 人工修正规则。

输出：

- 支持观点 A 的证据；
- 支持观点 B 的证据；
- 不确定信息；
- 相似历史案例；
- 每条证据的 Source Pointer；
- 明确 `human_decision_required=true`。

## 9. 人工反馈闭环

每次人工纠正保存：

- AI 原输出；
- 人工修正；
- 修正原因；
- 证据；
- 修改人；
- 时间。

后续用途：

- 修规则；
- Prompt/Few-shot；
- 训练项目分类器；
- 训练/微调 reranker；
- AI 准确率评估。

## 10. 异步任务和 n8n 边界

核心可靠任务：Redis + Celery。

- 官网同步；
- 文档解析；
- 向量生成；
- 变化比较；
- 大批量回填；
- 索引重建。

n8n 只负责外围通知和跨系统编排，不保存核心业务状态。

## 11. 部署模式

### Prototype

```bash
docker compose up --build
```

只启动 Web + Mock API。

### Real infrastructure

```bash
docker compose --profile real up --build
```

增加 PostgreSQL/pgvector、Redis、Worker、Scheduler。

### Peripheral automation

```bash
docker compose --profile automation up
```

增加 n8n。

生产环境必须把凭据、数据库和对象存储改为公司实际基础设施，禁止使用示例密码。
