# 官网来源 Connector（P3 官网接入）

> 交接文档 §7.2「官网 / 线上来源」、§10 Phase 2「官网监测」、§12.6「失败可见」的工程实现说明。
> 核心一句话：**失败可见、低置信度交人、关键数据可核验、系统可降级**。

## 1. 范围与实现边界

采集优先级（交接文档 §7.2）：

```text
官方 API / RSS  →  HTTP  →  Playwright  →  Browser Agent  →  人工兜底
                  ▲
             本模块只实现这一层
```

| 层级 | 状态 | 说明 |
|---|---|---|
| 官方 API / RSS | 未实现 | 若某来源有 RSS/API，应优先接入；`strategy` 字段已预留 |
| **HTTP** | **已实现** | `apps/api/app/connectors/official_http.py` |
| Playwright | 未实现（接口预留） | 见 §9 |
| Browser Agent | 未实现（接口预留） | 见 §9 |
| 人工兜底 | 通过 Source Health + 事件驱动 | `human_action_required` + `SOURCE_FAILED` |

依赖约束：**只使用 `httpx`**（已在 `requirements-real.txt`）。未引入 requests / playwright / bs4 / lxml；
HTML 解析使用标准库 `html.parser` 实现的最小 CSS 选择器引擎（支持 `tag`、`.class`、`#id`、`[attr=value]` 与后代组合）。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `config/sources.yaml` | **来源配置（新建）**：URL、解析规则、健康策略。3-5 个高频来源 |
| `apps/api/app/connectors/official_http.py` | `OfficialHttpConnector`、配置模型、错误归类、`match_project` |
| `apps/api/app/connectors/source_registry.py` | 加载配置、构建 Connector、`sync_all()` 汇总 |
| `apps/api/app/services/source_health_store.py` | `SourceHealthStore`、`ContentHashStore` |
| `tests/backend/test_official_http.py` | 本地 mock HTTP 服务测试（18 例） |

> `config/sources.example.yaml` 为既有只读示例，**未修改**。

## 3. 来源清单（⚠️ URL 全部未经人工验证）

`config/sources.yaml` 中每个来源都标注 `verified: false` 与 `note: 待业务确认`。
**这些 URL 是基于常识给出的初值，没有在真实网站上核对过**，禁止据此对外宣称「已接入」。

| id | 名称 | 是否启用 | 需登录 |
|---|---|---|---|
| `sichuan_gov_procurement` | 四川政府采购网 | ✅ | 否 |
| `sc_public_resource_trading` | 全国公共资源交易平台（四川省） | ✅ | 否 |
| `chengdu_public_resource_trading` | 成都市公共资源交易服务中心 | ✅ | 否 |
| `ceb_public_service` | 中国招标投标公共服务平台 | ✅ | 否 |
| `third_party_login_required_demo` | 第三方采招聚合源（示例） | ❌ | **是** |

**上线前必须由业务人员逐站确认**：`list_url` 是否可访问、`item_selector` 是否命中、是否需要登录、是否有验证码/反爬。
未确认前系统只做「可访问性探测 + 失败可见」，不做业务承诺。

## 4. 解析规则配置化（不在 Python 里硬编码站点结构）

`config/sources.yaml` 的 `parse` 段完全驱动解析，不同来源可用不同规则：

```yaml
parse:
  item_selector: "ul.notice-list li.notice-item, .article-list li"  # 逗号=并集（多套模板）
  title_selector: "a"                              # 标题（缺省取整个条目文本）
  link_selector: "a[href]"                         # 链接（缺省取首个 <a href>）
  date_selector: "span.date"                       # 日期元素（可选）
  date_regex: "(\\d{4}-\\d{2}-\\d{2})"             # 从日期文本/条目文本兜底抽取
  date_formats: ["%Y-%m-%d", "%Y/%m/%d"]           # 依次尝试
```

其它可配项：`timeout_seconds`、`max_retries`、`retry_backoff_seconds`（指数退避）、
`min_interval_seconds`（每来源最小请求间隔限流）、`user_agent`、`timezone_offset_hours`（默认东八区）。

> `user_agent` 会被自动清洗为 latin-1 安全值（HTTP 头不能含中文）；
> 配置里误填中文 UA 不会拖垮来源，但**请仍填写 ASCII 值**。

## 5. Source Health（交接文档 §7.2 逐项落地）

`SourceHealthStore`（内存 + 可选 ORM `source_health` 表）保存：

| 字段 | 含义 | 何时更新 |
|---|---|---|
| `last_success_at` | 最后成功时间 | 仅成功时刷新（失败不动，用于判断 stale） |
| `last_checked_at` | 最后检查时间 | 每次抓取（成功/失败）都刷新 |
| `consecutive_failures` | 连续失败次数 | 成功清零；失败累加 |
| `items_found` | 本次发现条数 | 成功写入解析条数；失败写 0 |
| `last_error` | 最近错误 | 失败写入 `kind: message` |
| `requires_login` | 是否需要登录 | 来自配置 |
| `human_action_required` | 是否需要人工处理 | 连续失败达阈值 或 需登录 |

查询接口：`record_success` / `record_failure` / `get` / `list_all` / `list_unhealthy`。

## 6. 不允许静默漏抓

**任何一次抓取失败都会 ①更新 SourceHealth ②产出 `SOURCE_FAILED` 事件 ③显式告警，绝不返回「同步正常」。**

失败归类（写入 `last_error` 的 `kind`）：

| kind | 触发 |
|---|---|
| `timeout` | 请求超时 |
| `connection_error` | 连接失败 / 被拒绝 |
| `http_403` / `http_404` / `http_5xx` | 非 2xx 状态码 |
| `anti_bot_page` | 命中验证码/反爬特征词（拿到的是拦截页，不是真实列表） |
| `too_many_redirects` / `transport_error` / `unknown_error` | 其它传输层异常 |
| `parse_no_items` | 选择器匹配 0 个条目 → **页面结构可能已变化**（潜在漏抓，按失败处理） |
| `parse_all_failed` | 匹配到节点但全部解析失败 |
| `selector_config_error` | `parse.item_selector` 写法非法（配置错误，必须显式暴露） |
| `parse_engine_error` | HTML 解析引擎异常 |
| `requires_login` | 来源需登录，Connector 不自动登录 |

事件语义：
- `SOURCE_FAILED`：`persistent=True`，`resolution_key=source_failed:<id>`；连续失败达阈值时 `severity=critical`。
- `NEW_OPPORTUNITY`：首次发现的公告。
- `ANNOUNCEMENT_CHANGED`：内容 hash 变化（可能是更正公告）。
- 单条解析失败**不拖垮整站**，只计数（`last_parse_errors` / `SyncResult.parse_errors`）。

`SourceRegistry.sync_all()` 返回 `SyncSummary`，其中 `ok=False` 且 `failed` 列出失败来源与原因；
`summary_text()` 会明确写「同步存在失败」，不会把失败说成正常。

## 7. hash diff 变化检测

`ContentHashStore` 保存每个外部条目的 `sha256(title + "\n" + text)`：

- 首次出现 → `NEW_OPPORTUNITY`；
- hash 与上次相同 → **不产出**任何变化事件；
- hash 变化 → `ANNOUNCEMENT_CHANGED`。

## 8. 中标公告项目匹配（确定性，禁止 LLM 拍板）

`match_project(announcement, existing_projects, threshold=0.78)`：

```text
confidence = 0.60 × 名称相似度 + 0.25 × 甲方一致 + 0.15 × 地区一致
```

- 名称相似度：去公告类型词（招标公告/中标公告/更正公告…）与标点空白后的 `SequenceMatcher` 比率；
- 甲方 / 地区：规范化后完全相等才计分。

- `confidence ≥ threshold` → `auto_fill_allowed=True`；
- `confidence < threshold` → `auto_fill_allowed=False`，**不得自动回填正式数据**，
  调用 `build_human_review_event()` 产出 `HUMAN_REVIEW_REQUIRED`（同屏展示候选项目与原公告，交接文档 FAILURE_MODES §2）。

阈值默认 0.78，与 `Settings.review_confidence_threshold` 一致。

## 9. 未来如何加 Playwright / Browser Agent（接口预留）

当前 `SourceConfig.strategy` 与 `SourceConnector` 抽象已为下一级策略预留：

1. 新增 `PlaywrightConnector(SourceConnector)`，同目录，复用同一个 `SourceHealthStore` 与事件产出；
2. 在 `config/sources.yaml` 为需要渲染的来源加 `strategy: playwright` 与对应 `parse` 规则；
3. 在 `source_registry.py` 按 `strategy` 选择 Connector 类；
4. 回退链：HTTP 连续失败达阈值 → 自动切 Playwright → 仍失败 → Browser Agent → 人工兜底（交接文档 §7.2 / FAILURE_MODES §1）。

新增 Playwright 需在 `requirements-real.txt` 增补依赖，本模块刻意不引入。

## 10. 与数据库的关系

- `SourceHealthStore(session_factory=...)`：写 `source_health` 表；
- `ContentHashStore(session_factory=..., organization_id=...)`：写 `source_objects` 表（需 organization_id）；
- 二者均为**可选增强**：数据库不可用时记录到 `persist_errors`，**不影响抓取主流程**。

## 11. 能力边界（交接文档 §13 六条，对号入座）

| §13 不可承诺项 | 本实现的边界 |
|---|---|
| ① 所有招采网站永远 100% 不漏抓 | 只做 HTTP；漏抓会以 `parse_no_items` / 失败事件**显式暴露**，不保证不漏 |
| ② 所有验证码/强反爬网站可无人值守 | **做不到**：命中反爬记为 `anti_bot_page` 失败，转人工/未来 Playwright |
| ③ 所有 AI 字段提取 100% 正确 | 本层只抽标题/链接/时间，不做 AI 提取；不确定的交人 |
| ④ 所有 Excel 都能 URL 精确跳单元格 | 不属本模块（文件接入范畴） |
| ⑤ AI 从竞对历史算出唯一最优报价 | **不做**，本模块无报价能力 |
| ⑥ 一个大模型训练替代数据清洗与索引 | 匹配用**确定性规则**，不用 LLM；索引不在本模块 |

补充：`config/sources.yaml` 中的 URL/选择器**未经真实站点验证**（`verified: false`），
不得对外宣称「已接入 N 个网站」。

## 12. 运行测试

```bash
cd BidOS_Prototype_v0.3
/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python \
  -m unittest discover -s tests/backend -p "test_official_http.py" -v
```

测试**不访问任何真实政府网站**：用标准库 `http.server` 在 `127.0.0.1` 随机端口起临时服务返回模拟 HTML。
覆盖：正常抓取、单条解析失败计数、404、超时、连接拒绝（`trust_env=False` 绕开本机代理）、反爬页、
页面结构变化、选择器逗号并集与非法选择器、连续失败阈值、需登录、非 ASCII UA 兜底、
hash diff、`match_project` 高/低置信、`sync_all` 失败不静默、真实配置加载。共 18 例全绿。

> 手工验证（非测试用例）时对 `config/sources.yaml` 中 4 个真实来源各发过 1 次 GET：
> 结果分别为 `parse_no_items`（占位选择器未命中）、`connection_error`（SSL 握手失败）、
> `anti_bot_page`（命中 captcha）。**全部被显式记录为失败，没有任何一个被误报为「同步正常」**，
> 这也印证了「URL 未经验证 + 失败可见」的设计意图。日常测试不访问真实站点。
