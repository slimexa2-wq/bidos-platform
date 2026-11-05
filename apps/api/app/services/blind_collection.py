"""采集渠道目录：专用接口优先，其余公开列表提取，失败按渠道展示。

采集节奏口径（用户 2026-10-10 修正）：**非工作日不采集**，且「工作日」要认
法定节假日与**调休补班日**（见 ``app.services.workdays``）—— 国庆调休的
2026-10-10 是周六但要上班，照采；国庆 10-01~10-07 里的工作日不采。
因此窗口跨度不能写死 24/72 小时，而是「距上一次成功采集」，并用
「上一个工作日」做日历兜底。
"""
from __future__ import annotations
from app.services.file_lock import file_lock
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import httpx
from app.domain import IngestedItem
from app.services import workdays
from app.services.blind_workspace import TZ, workbook_path, write_state, read_state, text_value
from app.services.notice_dates import pick_notice_datetime, record_notice_date

DATE = re.compile(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?')
NOTICE = re.compile(r'招标|招采|采购|比选|磋商|询价|竞价|遴选|征集|中标|成交|废标|流标|终止|结果公告')

#: 标题层面的**公告类型排除**（跨站通用兜底）。
#: ``NOTICE`` 是"收"的白名单，但它同时把 中标/成交/废标/流标/终止/结果公告 也放行了 ——
#: 这些不是"待投标的新招标信息"，会污染新信息池（中标结果另有结果监控链路负责）。
#: 只在标题上做减法，不改 ``NOTICE``，避免影响"某个渠道到底有没有列表"的判定。
EXCLUDED_NOTICE_TITLE = re.compile(
    r'终止公告|废标|流标|结果公告|中标公告|中标结果|成交公告|合同公告|合同公示|履约验收|验收公告'
)

#: 详情页取不到正文时的兜底前缀。写成显式标记而不是悄悄塞列表摘要：
#: 「原文没取到」和「原文就这些」必须能被读的人一眼区分（不许摘要冒充正文）。
#: 该行不含冒号，不会被字段抽取器当成标签。
LISTING_FALLBACK_MARK = '【正文未取到，以下为列表页摘要】'

#: 采集窗口跨度上限（小时）。窗口正常是「距上次成功采集」，不是固定 24 小时。
#: 2026 年最长的工作日间隔是春节那一段 **240 小时（10 天）**（2/14 调休上班 →
#: 2/15~2/23 放假 → 2/24 才上班）；留余量取 12 天，保证节后第一次采集能一次补全。
#: 超过上限就只补最近一段，避免一次要拉一整个月的公告、被列表页条数上限截断。
MAX_WINDOW_HOURS = 24 * 12

#: 单渠道单轮采集的**基准**条数（按 24 小时窗口）。
MAX_ITEMS_PER_CHANNEL = 60

#: 单渠道单轮的绝对条数上限。窗口越长需要读的条数越多（按天等比放大），
#: 封顶在这里，避免长假后单渠道无节制抓取。
MAX_ITEMS_CEILING = 240

#: 单个渠道单轮的时间预算（秒）。原来 HTML 分支 24 秒、JSON 分支 20 秒。
#: 提高条数上限后同步放宽，否则条数没到、时间先到，一样是截断。
CHANNEL_BUDGET_SECONDS = 45


def channel_item_limit(window_hours):
    """按窗口长度换算单渠道条数上限。

    假期后窗口会拉到 8~10 天，公告量是日常的 8~10 倍；固定 60 条会把更早的真实
    公告整段截断（列表页是从新到旧扫的，被截掉的是**更早**那些）。
    这里按天等比放大并封顶在 ``MAX_ITEMS_CEILING``。
    """
    days = max(1.0, float(window_hours or 24) / 24)
    return int(min(MAX_ITEMS_CEILING, round(MAX_ITEMS_PER_CHANNEL * days)))


def _last_collection_window_end():
    """上一次采集的窗口终点，作为本轮窗口起点（接着上次采，不漏停机期间的公告）。

    注意：**不能用 state 顶层的 lastSync** —— 那是桌面 Excel 同步（每 60 秒一次）
    的时间戳，拿它当窗口起点会把窗口缩到几分钟，等于大面积漏采。采集自己的时间戳
    在 collection 段里：优先 windowEnd（＝上一轮 started），其次 finishedAt / lastRun。
    """
    try:
        collection = read_state().get('collection') or {}
    except Exception:
        return None
    for key in ('windowEnd', 'finishedAt', 'lastRun'):
        stamp = collection.get(key)
        if not stamp:
            continue
        try:
            parsed = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    return None


def skip_reason_for(now):
    """非工作日返回应跳过的说明，工作日返回 None。

    口径（用户 2026-10-10）：「非工作日不采集」。注意是**非工作日**不是**周末** ——
    中国有调休，国庆调休的 2026-10-10（周六）是上班日，照采。

    抽成函数是为了能单测：判断藏在调度闭包里的话，这段关键逻辑就只能靠"看代码觉得对"。
    """
    if workdays.is_working_day(now.date()):
        return None
    return {
        'status': '非工作日跳过',
        'skipped': True,
        'day': now.date().isoformat(),
        'reason': '非工作日（周末或法定节假日），按口径不采集',
    }


def window_floor_start(window_end):
    """日历兜底的窗口起点 = **上一个工作日**的同一时刻。

    因为「非工作日不采集」，上一个工作日就是上一轮该采集的日子，所以它天然是
    窗口起点。这个兜底不依赖状态文件：
    * 普通周一 → 上一个工作日是周五 → 72 小时窗口；
    * 国庆后第一个工作日 2026-10-08 → 上一个工作日是 09-30 → 8 天窗口；
    * **有调休时也自动对**：2026-10-12（周一）的上一个工作日是 10-10（周六补班），
      只隔 2 天，而不是 3 天 —— 写死「周一＝72 小时」在这种场景就是错的。
    """
    previous = workdays.previous_working_date(window_end.date())
    return datetime.combine(previous, window_end.timetz())


def resolve_window_start(window_end, window_hours=None, last_end=None):
    """推导本轮采集的窗口起点（纯函数，便于单测）。

    规则：
    ① ``window_hours`` 显式指定时就用它（运维手动补采的逃生口），仍受上限约束。
    ② 否则接着「上次成功采集的终点」往下采 —— 已采过的项目天然不在窗口内，
       不会重复出现在台账里；机器停机也不会把停机期间的公告漏掉。
    ③ 上次终点晚于当前时刻（时钟回拨/状态写坏）不可信 → 走日历兜底；
       早于上限则截到上限（能补多少补多少，**不退回 24 小时白丢数据**）。
    ④ 状态文件丢失 → 日历兜底（上一个工作日同一时刻）。
    """
    earliest = window_end - timedelta(hours=MAX_WINDOW_HOURS)
    if window_hours:
        return max(window_end - timedelta(hours=window_hours), earliest)
    if last_end is not None:
        if earliest <= last_end <= window_end:
            return last_end
        if last_end < earliest:
            return earliest
    return max(window_floor_start(window_end), earliest)


def drop_non_ascii_cookies(client):
    """摘掉 Cookie 值里含非 ASCII 字符的 Cookie，返回摘掉的个数。

    实测缺陷（2026-10-09，采招网）：站点把中文写进 Cookie 值，httpx 在把 Cookie
    回填到请求头时按 ASCII 编码 → ``UnicodeEncodeError``，异常类型不属于
    ``httpx.HTTPError``，会一路冒到 ``_run`` 把整个渠道判成「采集失败」。

    这个坑原来被「单渠道最多 20 条」掩盖着（第 20 条就提前返回，走不到详情抓取）；
    条数上限提到 60 之后才会踩到。Cookie 本来就不该带非 ASCII 值，摘掉比让请求崩掉合理。

    注意：``str.encode('ascii')`` 只做判定，不改写站点返回的任何内容。
    客户端没暴露可迭代的 cookie jar 时直接返回 0（没有 cookie 可摘），
    这样测试里的轻量替身 client 也不会被这个加固动作带崩。
    """
    jar = getattr(getattr(client, 'cookies', None), 'jar', None)
    if jar is None:
        return 0
    try:
        cookies = list(jar)
    except TypeError:
        return 0
    dropped = 0
    for cookie in cookies:
        try:
            value = getattr(cookie, 'value', None)
            str(value or '').encode('ascii')
        except UnicodeEncodeError:
            jar.clear(cookie.domain, cookie.path, cookie.name)
            dropped += 1
    return dropped


class BlindCollection:
    def __init__(self, registry, session_factory):
        self.registry, self.sessions = registry, session_factory

    def catalog(self):
        import openpyxl
        source = workbook_path('*盲投渠道表*.xlsx')
        if not source:
            return []
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        channels = []
        for ws in wb:
            if '渠道' not in ws.title:
                continue
            # A-D是公开来源目录；其后的凭据不读取、不传出。
            for row in ws.iter_rows(min_row=2, max_col=4, values_only=True):
                name, url = text_value(row[1]), text_value(row[2])
                if url.startswith('www.'):
                    url = 'https://' + url
                if name and re.match(r'https?://', url):
                    channels.append({'name': name, 'url': url, 'frequency': text_value(row[3])})
        wb.close()
        return channels

    def _public_listing(self, channel, window_end=None, window_start=None):
        """按**逐站实测策略**取公告；没有策略的渠道退回通用首页解析。

        109 个渠道的失败原因各不相同（SPA 外壳 / 真实列表在栏目页而非首页 /
        证书链不合规 / 列表由 XHR 回填），用一条通用规则兜不住 —— 实测只有 18 个
        渠道能读出公告。策略表由 deliverables/blind-channel-coverage-20261009
        的「HTTP 探测 + 浏览器渲染」两轮实测合成（见 channel_strategies 模块）。
        """
        from bs4 import BeautifulSoup
        from app.services.tender_details import notice_text, blocked_text
        from app.services import channel_strategies as cs
        strategy = cs.strategy_for(channel['name'])
        # 该站点实测要先排除的栏目路径（如四川站的「签约履行/终止公告/履约验收」栏目）：
        # 类型只写在栏目路径里，标题完全看不出来，必须在遍历链接时就挡掉。
        excluded_paths = cs.excluded_paths(channel['url'])
        # 只要实测出的是 JSON 接口就走 JSON 分支：list_ok 表示"接得上但近 7 天没新公告"，
        # 不等于没接通 —— 早先只放行 verified 会导致这些渠道被当成 HTML 页面去解析，
        # 结果是拿一个 JSON 接口当网页抓，必然 0 条。
        if strategy and strategy.get('transport') == 'json':
            return self._json_listing(channel, strategy, window_end)
        if (urlparse(channel['url']).hostname or '') in ('www.sdicc.com.cn', 'sdicc.com.cn'):
            return self._sdic_listing(channel, window_end)
        now = window_end or datetime.now(TZ)
        cutoff = window_start or (now - timedelta(hours=24))
        # 窗口越长，需要读的条数越多（假期后是日常的 8~10 倍）。
        items_limit = channel_item_limit((now - cutoff).total_seconds() / 3600)
        found = []
        blocked_details = 0
        started = time.monotonic()
        # 列表页优先用实测命中的那个（首页常常没有带日期的公告列表）
        target = (strategy or {}).get('list_url') or channel['url']
        client_kwargs = {
            'timeout': 12, 'follow_redirects': True, 'trust_env': False,
            'headers': {'User-Agent': 'BidOS/0.3 public-notice-collector'},
        }
        if strategy and strategy.get('tls') == 'relaxed':
            client_kwargs['verify'] = cs.ssl_context('relaxed')
        with httpx.Client(**client_kwargs) as client:
            res = client.get(target); res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            page_text = soup.get_text(' ', strip=True)
            if any(t in page_text[:1000] for t in ('验证码', '安全验证', '访问过于频繁')):
                return channel, [], '需人工处理', '网站要求验证'
            # 站点可能往 Cookie 里塞中文；不摘掉的话后续每个请求都会抛 UnicodeEncodeError。
            drop_non_ascii_cookies(client)
            seen = set()
            dated = 0
            for a in soup.select('a[href]'):
                title = a.get_text(' ', strip=True) or a.get('title', '')
                if len(title) < 8 or not NOTICE.search(title):
                    continue
                if EXCLUDED_NOTICE_TITLE.search(title):
                    continue
                url = urljoin(str(res.url), a['href'])
                if urlparse(url).scheme not in ('http', 'https') or url in seen:
                    continue
                path = urlparse(url).path
                if excluded_paths and path.startswith(excluded_paths):
                    continue
                # 查列表条目，禁止从整页其它位置借日期。
                node = a
                date_match = None
                for _ in range(3):
                    snippet = node.get_text(' ', strip=True)
                    if sum(bool(NOTICE.search(link.get_text(' ', strip=True))) for link in node.select('a[href]')) > 1:
                        break
                    if len(snippet) <= 1500:
                        date_match = DATE.search(snippet)
                    if date_match:
                        break
                    node = node.parent
                    if node is None:
                        break
                if not date_match:
                    continue
                # 行内常常同时有「发布日」和「投标截止/开标时间」。原来取行内第一个日期，
                # 会把未来的截止日当成发布时间 —— 公告随即被判成"还没发布"而漏掉。
                # 统一按 notice_dates 的口径：取不晚于今天的最新一个日期。
                picked, date_only = pick_notice_datetime(snippet)
                if picked is None:
                    continue
                dt = picked.replace(tzinfo=TZ)
                dated += 1
                if dt > now or (dt + timedelta(days=1) <= cutoff if date_only else dt < cutoff):
                    continue
                seen.add(url)
                if len(found) >= items_limit or time.monotonic() - started > CHANNEL_BUDGET_SECONDS:
                    return channel, found, '部分读取', '本轮达到采集上限，可能还有未读取的公告'
                try:
                    detail = client.get(url, timeout=6); detail.raise_for_status()
                    body = notice_text(detail.text)
                    if blocked_text(body):
                        blocked_details += 1
                except (httpx.HTTPError, UnicodeError):
                    # UnicodeError：站点 Cookie/响应头带非 ASCII 字符，httpx 编码请求头时抛错。
                    # 取不到正文时**显式标注**这是列表页摘要，不许让摘要冒充公告正文
                    # （用户纪律：缺正文就说缺，不用模拟过的内容充成功）。
                    body = LISTING_FALLBACK_MARK + '\n' + snippet
                found.append(IngestedItem(external_id=hashlib.sha1(url.encode()).hexdigest(),
                    source_id='channel_' + hashlib.sha1(channel['url'].encode()).hexdigest()[:12], title=title, url=url,
                    published_at=dt, text=body, fields={'date_precision': 'day' if date_only else 'second', 'channel_name': channel['name'], 'trust_level': 'public'}))
            if not dated:
                return channel, [], '需接入', '公开页面未提取到带日期的公告列表'
        return channel, found, '部分读取' if blocked_details else '已读取', f'{blocked_details}条公告正文需要网站验证，待补全' if blocked_details else ''

    def _json_listing(self, channel, strategy, window_end=None, window_start=None):
        """直连渠道自己的 JSON 列表接口（策略由浏览器渲染时监听到，已复验可脱离浏览器调用）。

        这类站点（阳光采购平台 / 供应链门户）首页是 SPA，HTML 里没有列表；
        但页面自己会去拉一个公开接口。直接打这个接口比渲染页面**又快又稳**：
        无浏览器依赖、无 DOM 结构依赖，改版只要接口不变就还能用。
        """
        import json as _json
        from app.services.tender_details import notice_text
        from app.services import channel_strategies as cs
        call = strategy.get('apiCall') or {}
        mapping = call.get('recordMap') or {}
        url = call.get('url')
        if not url or not mapping.get('title'):
            return channel, [], '需接入', '接口字段映射未确认，待复核后启用'
        now = window_end or datetime.now(TZ)
        cutoff = window_start or (now - timedelta(hours=24))
        # 窗口越长，需要读的条数越多（假期后是日常的 8~10 倍）。
        items_limit = channel_item_limit((now - cutoff).total_seconds() / 3600)
        found = []
        started = time.monotonic()
        headers = {'User-Agent': 'BidOS/0.3 public-notice-collector',
                   'Accept': 'application/json', 'Content-Language': 'zh-CN'}
        if call.get('postData'):
            headers['Content-Type'] = 'application/json'
        with httpx.Client(timeout=12, follow_redirects=True, trust_env=False, headers=headers,
                          # 与 HTML 分支同一套 TLS 口径：部分国企站的证书链/重协商协商
                          # 不满足 OpenSSL 3.x 默认要求（浏览器能开、httpx 默认握手就失败）
                          verify=cs.ssl_context(strategy.get('tls', 'strict'))) as client:
            if (call.get('method') or 'GET').upper() == 'POST':
                res = client.post(url, content=call.get('postData') or '{}')
            else:
                res = client.get(url)
            res.raise_for_status()
            try:
                payload = res.json()
            except ValueError:
                return channel, [], '需接入', '接口返回的不是 JSON，待复核'
            records = payload
            for part in (call.get('recordsPath') or '').split('.'):
                if part:
                    records = records.get(part) if isinstance(records, dict) else None
            if not isinstance(records, list):
                return channel, [], '需接入', '接口记录路径未命中，待复核'
            dated = 0
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                title = str(rec.get(mapping['title']) or '').strip()
                if len(title) < 8 or not NOTICE.search(title):
                    continue
                # 接口记录里发布日 / 报名截止 / 开标时间往往同时存在，统一按 notice_dates 口径取
                day, future = record_notice_date(rec)
                if day is None:
                    continue
                if future:
                    # 该记录里只有截止/开标类日期，日期不可靠，不作为发布日采信
                    continue
                dated += 1
                dt = datetime(day.year, day.month, day.day, tzinfo=TZ)
                if dt > now or dt + timedelta(days=1) <= cutoff:
                    continue
                # 详情链接：接口给全 URL 就用它；只给 id 的按站点根兜底（不编造路径）
                link = ''
                if mapping.get('url') and rec.get(mapping['url']):
                    link = urljoin(channel['url'], str(rec[mapping['url']]))
                elif call.get('urlBase') and mapping.get('id') and rec.get(mapping['id']):
                    link = call['urlBase']
                if not link:
                    link = channel['url']
                if len(found) >= items_limit or time.monotonic() - started > CHANNEL_BUDGET_SECONDS:
                    return channel, found, '部分读取', '本轮达到采集上限，可能还有未读取的公告'
                # 旧实现把接口记录整段 json.dumps 当正文，字段正则于是在 JSON 串上乱抓
                # （实测 项目编号 抓到 1284 字的转义 HTML）。改成渲染成「字段：值」可读文本，
                # 内含 HTML 的字段走正文提纯、XML 元数据（<CNG_BID_CALL_END value="…"/>）展开。
                from app.services.tender_details import record_text
                body = record_text(rec) or _json.dumps(rec, ensure_ascii=False)[:4000]
                found.append(IngestedItem(
                    external_id=hashlib.sha1((link + title).encode()).hexdigest(),
                    source_id='channel_' + hashlib.sha1(channel['url'].encode()).hexdigest()[:12],
                    title=title, url=link, published_at=dt, text=body,
                    fields={'date_precision': 'day', 'channel_name': channel['name'],
                            'trust_level': 'public'}))
            if not dated:
                return channel, [], '需接入', '接口可访问但未提取到带日期的公告'
        return channel, found, '已读取', ''

    def _sdic_listing(self, channel, window_end=None, window_start=None):
        from app.services.tender_details import sdic_entries, notice_text
        now = window_end or datetime.now(TZ)
        cutoff = window_start or (now - timedelta(hours=24))
        # 窗口越长，需要读的条数越多（假期后是日常的 8~10 倍）。
        items_limit = channel_item_limit((now - cutoff).total_seconds() / 3600)
        found = []
        started = time.monotonic()
        with httpx.Client(timeout=10, follow_redirects=True, trust_env=False) as client:
            response = client.get(channel['url']); response.raise_for_status()
            drop_non_ascii_cookies(client)
            for entry in sdic_entries(response.text, channel['url']):
                if not entry['published']:
                    continue
                published = datetime.fromisoformat(entry['published']).replace(tzinfo=TZ)
                if published > now or published + timedelta(days=1) <= cutoff:
                    continue
                if len(found) >= items_limit or time.monotonic() - started > CHANNEL_BUDGET_SECONDS:
                    return channel, found, '部分读取', '本轮达到采集上限'
                try:
                    detail = client.get(entry['url'], timeout=6); detail.raise_for_status()
                    body = notice_text(detail.text)
                except (httpx.HTTPError, UnicodeError):
                    body = entry['title']
                found.append(IngestedItem(external_id=hashlib.sha1(entry['url'].encode()).hexdigest(),
                    source_id='channel_' + hashlib.sha1(channel['url'].encode()).hexdigest()[:12],
                    title=entry['title'], url=entry['url'], published_at=published, text=body,
                    fields={'date_precision': 'day', 'channel_name': channel['name'], 'trust_level': 'official',
                            'registration_deadline': entry['deadline']}))
        return channel, found, '已读取', ''

    def run(self, resume=False, window_hours=None):
        with file_lock('/tmp/bidos_blind_collection.lock', blocking=False) as _lk:
            if not _lk.acquired:
                return read_state().get('collection') or {'status': '采集中'}
            return self._run(resume, window_hours=window_hours)

    def _run(self, resume=False, window_hours=None):
        from app.services.announcement_landing import AnnouncementLandingService
        from app.connectors.base import ConnectorContext
        window_end = datetime.now(TZ)
        started = window_end.isoformat()
        # 窗口 = 「距上一次成功采集」，不是固定 24 小时（规则见 resolve_window_start）：
        # ① 平时：昨天 16:30 → 今天 16:30，天然不重不漏（去重键再兜一层）；
        # ② 周末不排采集：周一是 72 小时（周五 16:30 → 周一 16:30），由 window_hours 兜底；
        # ③ 机器停机：窗口自动拉长把停机期间补回来，但不超过 MAX_WINDOW_HOURS。
        window_start = resolve_window_start(window_end, window_hours, _last_collection_window_end())
        from app.services.blind_workspace import BlindWorkspace
        expired = BlindWorkspace(self.sessions).expire_candidates()
        channels, new_count = [], 0
        catalog = self.catalog()
        previous = read_state().get('collection') or {}
        completed_names = set()
        if resume and previous.get('lastRun', '')[:10] == started[:10]:
            channels = list(previous.get('channels') or [])
            new_count = previous.get('newCount') or 0
            from collections import Counter
            names = Counter(c['name'] for c in catalog)
            names.update(c.name for c in self.registry.configs if c.enabled)
            completed_names = {c['name'] for c in channels
                               if names[c['name']] == 1 and c.get('status') == '已读取'}
            # 同名的多个网址重新核对，避免把一处失败套到另一处来源。
            channels = [c for c in channels if c['name'] in completed_names]
        write_state({'collection': {'lastRun': started, 'status': '采集中', 'catalogCount': len(catalog), 'newCount': 0, 'channels': []}})
        native_hosts = {urlparse(c.list_url).hostname for c in self.registry.configs if c.enabled}
        landing = AnnouncementLandingService(self.sessions, organization_id='org_xiangneng', clock=lambda: window_end)
        def native(config):
            from dataclasses import replace
            connector = self.registry._build_connector(replace(config, timeout_seconds=10, max_retries=1), time.sleep)
            result = self.registry._sync_connector(connector, ConnectorContext(since=window_start, limit=1000))
            self.registry._run_landing(result)
            status = '需人工处理' if result.human_action_required else '采集失败' if not result.ok or result.landing_error else '已读取'
            return {'name': config.name, 'status': status, 'message': (result.error or result.landing_error or '')[:160]}, result.candidate or 0

        remaining = [c for c in catalog if urlparse(c['url']).hostname not in native_hosts and c['name'] not in completed_names]
        # 每个来源只运行一次；4路并发，单站失败不拖住其它来源。
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            for config in self.registry.configs:
                if config.enabled and config.name not in completed_names:
                    futures[pool.submit(native, config)] = ('native', {'name': config.name})
            for c in remaining:
                futures[pool.submit(self._public_listing, c, window_end, window_start)] = ('public', c)
            for future in as_completed(futures):
                kind, c = futures[future]
                try:
                    if kind == 'native':
                        report_row, added = future.result()
                        channels.append(report_row)
                        new_count += added
                    else:
                        _, items, status, message = future.result()
                        if items:
                            outcome = landing.land_items(source_id=items[0].source_id, source_name=c['name'], items=items)
                            new_count += outcome.candidate
                        channels.append({'name': c['name'], 'status': status, 'message': message})
                except Exception as exc:
                    message = '网站访问超时' if isinstance(exc, httpx.TimeoutException) else f'网页返回 {exc.response.status_code}，请核对来源网址或访问限制' if isinstance(exc, httpx.HTTPStatusError) else '网站连接失败' if isinstance(exc, httpx.RequestError) else f'公告提取失败（{type(exc).__name__}）'
                    channels.append({'name': c['name'], 'status': '采集失败', 'message': message})
                write_state({'collection': {'lastRun': started, 'status': '采集中', 'catalogCount': len(catalog), 'newCount': new_count, 'channels': list(channels)}})
        failed = sum(c['status'] != '已读取' for c in channels)
        report = {'lastRun': started, 'finishedAt': datetime.now(TZ).isoformat(), 'status': '部分完成' if failed else '完成',
                  'catalogCount': len(catalog), 'total': len(channels),
                  'usable': sum(c['status'] == '已读取' for c in channels),
                  'failed': failed, 'newCount': new_count,
                  'channels': channels, 'expired': expired, 'windowStart': window_start.isoformat(),
                  'windowEnd': started,
                  'windowHours': round((window_end - window_start).total_seconds() / 3600, 2),
                  'itemsPerChannelLimit': channel_item_limit((window_end - window_start).total_seconds() / 3600),
                  'dailyAt': '16:30', 'timezone': 'Asia/Shanghai',
                  'workdayCalendar': workdays.calendar_info(),
                  'schedulePolicy': '非工作日不采集（认法定节假日与调休补班日）；窗口＝距上次采集，并用上一个工作日兜底',
                  'datePolicy': '按窗口（上次采集 → 本次）筛选：有时分秒按时间点比较；仅公布日期的按日期区间与窗口相交保留，不虚构发布时间'}
        write_state({'collection': report})
        from app.services.tender_details import TenderDetailsService
        TenderDetailsService(self.sessions).refresh_missing(catalog)
        return report
