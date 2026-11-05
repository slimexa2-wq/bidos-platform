"""盲投渠道的**逐站获取办法**（109 个渠道，实测生成，不靠猜）。

背景：原先所有渠道都走同一条通用路径（拿首页 HTML → 找带日期的公告链接），
结果是 109 个渠道里只有 18 个能读出公告；其余失败原因各不相同（SPA 外壳、
真实列表在栏目页而非首页、证书链不合规、列表数据由 XHR 回填……），
用一条通用规则去覆盖必然大面积失败。

本模块读取 `config/blind_channel_strategies.json`：每个渠道记下**实测有效**的
获取办法（列表页地址 / 解析方式 / TLS 级别 / JSON 接口的 method+body+记录路径），
`BlindCollection` 按名字取用；拿不到策略的渠道，退回原来的通用路径。

纪律（交接文档 §7.2）：
* 只有实测抓到带日期公告、且最新一条在 7 天内的渠道才标 `verified`；
* `tls: relaxed` 只用于证书链不合规的站，**不用于绕过反爬**；
* 验证码 / 登录墙一律不绕过（`status: manual`），交人工兜底；
* 配置由 `deliverables/blind-channel-coverage-20261009/evidence/build_strategies.py`
  合成，原始证据在同目录的 `probe-result.json` / `discovery-result.json` / `render-result.json`。
"""
from __future__ import annotations

import json
import ssl
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
STRATEGY_FILE = ROOT / "config/blind_channel_strategies.json"

# 允许进入采集编排的状态：其余状态只做"失败可见"，不参与抓取
COLLECTABLE = ("verified", "list_ok")

#: 站点栏目排除（**逐站实测**，2026-10-10）。
#:
#: 公共资源交易类站点的"公告列表"里混着多种公告类型，只有**公告**类才是
#: 待投标的新信息。用户反馈「新信息格式内容完全不对」时实测发现：四川站
#: 一次采到 72 条，其中 19 条根本不是待投标项目 —— 类型只写在栏目路径与
#: 详情页面包屑里，标题完全看不出来（例如标题就叫「物业管理采购」）。
#: 逐个开页面取证得到下表（括号内为面包屑原文）：
#:
#:   ggzyjy.sc.gov.cn（四川省公共资源交易中心）
#:     /jyxx/002002/002002004/  政府采购-签约履行  ← 附件是「…服务合同.pdf」，已签约
#:     /jyxx/002002/002002005/  政府采购-终止公告  ← 正文「符合条件的供应商不足三家」
#:     /jyxx/002002/002002006/  政府采购-履约验收  ← 正文「一、合同编号…二、合同名称…」
#:     /jyxx/002001/002001012/  工程建设-招标文件  ← 与 002001001「招标公告」同项目，重复
#:
#: 保留：002002001 政府采购-采购公告、002010001 国企采购-公告信息、
#:       002007001 其它类别-公告信息、002011001 农村产权-交易公告、
#:       002008001 代理机构比选-比选公告、002001001 工程建设-招标公告。
SITE_EXCLUDE_PATH = {
    'ggzyjy.sc.gov.cn': ('/jyxx/002002/002002004/', '/jyxx/002002/002002005/',
                         '/jyxx/002002/002002006/', '/jyxx/002001/002001012/'),
}


def excluded_paths(url: str) -> tuple:
    """该站点实测应排除的栏目路径前缀；没有实测记录时返回空元组。"""
    host = urlparse(url or '').hostname or ''
    for site, prefixes in SITE_EXCLUDE_PATH.items():
        if host == site or host.endswith('.' + site):
            return prefixes
    return ()



# SSL_OP_LEGACY_SERVER_CONNECT = 0x4：允许与不做 RFC 5746 安全重协商的老服务器握手。
# Python 3.12 才提供同名常量，3.11 只能用数值。不加会报
# SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED —— 实测吉利电子招标平台需要它才能通。
OP_LEGACY_SERVER_CONNECT = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def ssl_context(tls: str = "strict") -> ssl.SSLContext:
    """按策略给出的 TLS 级别建上下文。

    分层含义（都属**信任/兼容级别调整，不是反爬绕过**）：
      strict  系统信任库 + 默认安全等级，只额外允许老服务器重协商
      relaxed 再降一级：不校验证书链（部分政务/国企站的证书链不满足
              OpenSSL 3.x 的 SECLEVEL=2，curl 与浏览器能通、httpx 默认配
              置握手直接失败，会被误判成"站点不可达"）
    """
    ctx = ssl.create_default_context()
    if tls == "relaxed":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= OP_LEGACY_SERVER_CONNECT
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:  # pragma: no cover - 取决于底库
        pass
    return ctx


@lru_cache(maxsize=1)
def _table() -> tuple[dict, dict]:
    """返回 (按渠道名的策略表, 元信息)。文件缺失/损坏时不抛异常，退化为空表。"""
    if not STRATEGY_FILE.is_file():
        return {}, {}
    try:
        payload = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    table = {c.get("name"): c for c in payload.get("channels") or [] if c.get("name")}
    return table, {"version": payload.get("version"), "generatedAt": payload.get("generatedAt"),
                   "byStatus": payload.get("byStatus") or {}}


def strategy_for(name: str):
    """取某个渠道的获取办法；没有实测策略时返回 None（调用方走原通用路径）。"""
    return _table()[0].get(name)


def overview() -> dict:
    """给接口/页面用的一句话概览：各状态渠道数 + 配置生成时间。"""
    _, meta = _table()
    return {"channels": len(_table()[0]), **meta}


def rows_from_records(records: list, mapping: dict) -> list:
    """按字段映射把接口返回的记录数组转成统一的公告条目。

    mapping 形如 {"title": "title", "url": "linkUrl|id", "date": "publishTime"}
    （`url` 支持 `字段a|字段b` 表示用 a 拼接、缺失时退回 b 作为路径参数）。
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get(mapping.get("title") or "title") or "").strip()
        if not title:
            continue
        out.append({"title": title, "date": str(rec.get(mapping.get("date") or "date") or "")[:32],
                    "raw": rec})
    return out
