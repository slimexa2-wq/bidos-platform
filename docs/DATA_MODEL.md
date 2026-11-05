# 核心数据模型

## Project

系统统一的项目 ID。一个项目可以关联多条公告、多份文件、多张表记录、多家竞对和多个阶段。

## Entity

甲方、竞对、代理机构、公司主体等统一实体。

为解决“XX公司 / XX有限公司 / XX集团成都分公司”问题，使用 `entity_aliases` 保存别名，不直接靠 LLM 每次猜。

## SourceObject

一次原始来源对象，如：

- 某官网招标公告；
- 某中标公告；
- 某飞书表记录；
- 某 Excel 行；
- 某 PDF。

## SourcePointer

SourceObject 里的具体可核验位置，例如：

- PDF P73 第 12.4 条；
- `2026未参与` Sheet 第64行；
- 飞书 Record ID；
- 官方公告 URL。

## Event

“发生了值得关注的变化”，不是普通消息。

## HumanDecision

参与/不参与、报价讨论结论、风险接受等人工决策，单独保存，绝不和 AI 建议混在一起。

## Feedback

AI 输出被人工纠正后的训练/优化素材。

## AuditLog

谁、什么时候、改了什么、依据是什么。


## v0.3 项目结束日期

`projects.project_end_at` 表示业务上的项目/服务结束日期，`project_end_date_kind` 区分 exact / estimated / manual。
未中标项目同样维护结束日期，因为当前中标服务商的服务周期结束后可能重新招标。

## v0.3 保证金台账

`bid_bond_ledger` 独立保存保证金的缴纳、应退、实际退回及证据链。保证金事件使用持久 Event，只有业务状态解决后才关闭。
