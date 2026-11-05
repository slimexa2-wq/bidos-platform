# v0.2 架构完善记录

- 将后端从单文件 Mock API 拆为 connectors / ingestion / indexing / documents / AI / services。
- 增加 Source Pointer 统一证据模型。
- 增加来源健康模型与同步接口。
- 增加确定性 Event Engine 示例和测试。
- 增加 Hybrid Search 原型接口，正式版对应 PostgreSQL + pgvector + reranker。
- 增加 Decision Support API，输出支持/谨慎/未知证据，强制人工决策。
- 增加 Feedback 与 Audit 闭环。
- 增加 Docling 可插拔解析器。
- 增加 PostgreSQL/pgvector Schema。
- 增加 Redis/Celery Worker 与 Scheduler 骨架。
- 明确 n8n 只负责外围自动化。
- 修正 Docker Web → API 反向代理，避免静态 Vite 环境变量运行时失效问题。
- 补充 API Mock 数据，`docker compose up` 不再出现空业务页。
- 前端 DataProvider 增加来源健康、决策参考、反馈和人工决策契约。
- 增加功能落地矩阵、失败兜底、安全、可观测性、真实数据接入文档。
