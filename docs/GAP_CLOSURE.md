# 缺口收尾说明（GAP_CLOSURE）

> 验收矩阵（acceptance-matrix-2026-09-24.md）❌ 项中两项已实现。本文档说明能力边界与 API 签名。

## 1. 竞对重合分析（原 ❌「项目重合分析」）

**文件**：`apps/api/app/services/overlap_analysis.py`

回答业务问题：某竞对中标的项目里，多少个我司也参与过？我司报价比它中标价高/低多少？重合项目集中在我司哪些区域/客户？

**API**：`GET /api/competitors/{competitor_name}/overlap`

响应结构：
```json
{
  "competitor": "四川宝航人力资源有限公司",
  "matched_entity": true,          // 是否在 entities 表登记
  "aliases": ["宝航人力"],
  "sample_sizes": {"competitor_won": 45, "our_participated": 1097, "overlap": 45},
  "overlap_count": 45,
  "projects": [ /* 每项含 our_price/winning_price/price_diff/price_ratio */ ],
  "regions": {"成都": 30, ...},
  "clients": {...},
  "notices": [ /* 数据不足的诚实说明 */ ]
}
```

**边界（重要）**：
- 竞对名优先走 entity_aliases 规范化；未登记的名字直接匹配但带 notice 建议补录
- 重合判定 = 同 project_id 或同规范化项目名；样本数随结果返回
- 数据库不可用 → 503；mock 模式 → 返回带说明的空结果（不假装成功）

## 2. 整标检查（原 ❌「整标检查」）

**文件**：`apps/api/app/services/bid_document_check.py`

**API**：`POST /api/documents/check`，body 即文档结构（与前端 documents 结构兼容）：

```json
{
  "sections": [{"title": "技术方案", "content": "拟投入 45 人……"}],
  "meta": {
    "headcount": 45,
    "amount": 12000000,
    "dates": [{"label": "服务期", "start": "2026-10-01", "end": "2028-09-30"}]
  }
}
```

响应：`{"issues": [...], "error_count": N, "warning_count": N, "notice_count": N}`
Issue 结构：`{"severity": "error|warning|notice", "rule", "message", "location"}`

**5 条规则**（均可单条开关）：
| 规则 | 检查内容 |
|---|---|
| headcount_consistency | 全文"45 人/60 名"与 meta.headcount 冲突 |
| amount_consistency | 全文金额与 meta.amount 冲突（容差 1%，<100 元视为页码不参与） |
| date_consistency | 结束早于开始等日期矛盾 |
| required_sections | 缺章节（构造时可传） |
| required_keywords | 缺资格关键词（构造时可传） |

**边界**：
- meta 缺 headcount/amount → 该规则降级为 notice 提示，**不硬编造默认值**
- 检查对象是传入文档，本轮不落库（存储属后续阶段）
- 与 Docling 解析（P4 规划）对接后，可直接喂解析出的章节文本

## 3. 验收矩阵状态更新

- 「竞对分析 / 项目重合分析」：❌ → ✅（真库实证：宝航 45 个重合项目，价格缺失诚实提示）
- 「标书制作 / 整标检查」：❌ → ✅（规则引擎 5 规则 + 12 用例全绿；真库冒烟检出人数矛盾）

剩余 ❌：报名材料缺项自动识别、公司资料库调用、材料包生成（三项同属报名管理资料体系，依赖公司资质库数据，建议下一阶段做）。

## 4. 报名资料体系（原 ❌ 3 项全部完成，19:40）

**文件**：`apps/api/app/services/registration_materials.py` + `tests/backend/test_registration_materials.py`（7 用例）

**API**（均已在 main.py 挂载）：
- `POST /api/registrations/identify-materials` —— 材料缺项自动识别。body 可含 `announcement_text`（公告文本）/ `bid_amount`（保证金金额，0 → 凭证标 not_required）/ `library_root`（公司资料库目录，也可用环境变量 `BIDOS_PROFILE_LIBRARY`）。
- `POST /api/registrations/material-package` —— 材料包生成。**只生成 manifest 清单**（文件路径+SHA-256），绝不复制/修改原文件；missing 清单如实列出，`complete=false` 时禁止视为已齐。

**材料状态机**：`ready`（资料库有候选）/ `missing`（公告要求但库没有）/ `confirm`（公告没提但建议准备，交人工）/ `not_required`（如保证金=0）。

**反幻觉**：识别不出的公告要求（含"提供/提交"但没命中规则表）进 `unknown_requirements`，绝不硬套材料类型；规则表 `MATERIAL_RULES` 可配置外置。

**验收矩阵状态**：3 项 ❌ → ✅。**49 项中不再有未实现项**（⛔3 项为规划期）。
