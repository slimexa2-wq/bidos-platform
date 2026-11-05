"""新信息的原公告字段提取与补全；只整理事实，不替人决定参与。

2026-10-10 修复（用户反馈「收集的盲投新信息格式内容完全不对」）
--------------------------------------------------------------
三个已复现的根因，逐个在下面注明：

1. **正文提纯把行拆碎**：旧 ``notice_text`` 用 ``get_text('\\n')``，会给
   *每一个* 子串之间插换行。实测「四川省公共资源交易信息网」的正文是
   ``<p>1.采购项目编号：<span>SC01162220260014</span></p>``，于是标签行与
   值行被拆成两行，而 ``extract_details`` 的右边界靠"下一个被识别的标签"
   定位 → 值一路吞到文末。实测 ``代理机构`` 最长 1532 字（含「二、资金情况」
   「五、合格供应商…」整段）、``项目情况简述`` 3199 字。
   现改为**块级标签才换行、行内标签不换行**，并在提纯后做一次碎片合并。

2. **正文容器选不中 → 整页兜底**：旧选择器（``.dg-notice-detail, article,
   #content`` …）在实测站点上**全部落空**，退回 ``soup`` 整页，于是面包屑
   （``首页 > 交易信息``）和页脚链接农场（``国家 / 省级 / 市级`` 两三百字）
   都进了正文。实测 72 条候选里 16 条 ``代理机构`` 字段吃进省市导航。
   现在的做法：优先命中内容容器 → 只在 ``<body>`` 内找 → 命中即用；
   再叠加噪声容器剔除、页脚标记截断、尾部链接农场截断三重兜底。

3. **API 记录被当正文**：``blind_collection._json_listing`` 旧实现把接口返回
   的记录 ``json.dumps`` 后整段当正文（33 个渠道走这条分支），字段正则于是
   在 JSON 串上乱抓 —— 实测 ``项目编号`` 抓到 1284 字的转义 HTML。
   现新增 :func:`record_text`，把记录渲染成 ``字段：值`` 的可读文本，
   顺带解开其中的 HTML 正文块与 XML 元数据（``<CNG_BID_CALL_END value="…"/>``）。

2026-10-10 追加修复（VERSION 5→6→7→8，同一轮反馈的收尾）
--------------------------------------------------------
4. **机构名吃进联系方式尾巴**：原文常把代理机构排成
   ``XX招标有限公司 地 址：… 联 系 人：… 电 话：…``，其中「地 址」字间带空格，
   与 ``LABEL_RE`` 的「标签名后紧跟冒号」口径不匹配 → 一个字段吃进三条信息
   （实测 ``代理机构`` 65/84/93 字）。现由 :func:`_org_value` 在第一个联系方式
   子标签处截断；同一闸还挡掉模板占位垃圾（实测 ``代理机构：地址:地址:``），
   判为垃圾时不落库，继续试下一个同义标签。（VERSION 5→6）

5. **服务期限吞掉整段资格要求**：原文写「本项目是否接受联合体参与：」，
   而标签白名单里只有「是否接受联合体投标 / 是否接受联合体」，
   且该句紧跟在上一行值的同一行后面，不满足 ``GENERIC_LABEL_RE`` 的行首条件
   → 右边界找不到，``服务期限`` 一路吞到下一个能识别的标签（实测最长 115 字，
   把「本项目是否接受联合体参与：采购包1：不接受…」整段吃进来）。
   两步修复：① 把「是否接受联合体参与」补入白名单；（VERSION 6→7）
   ② 新增 :func:`_with_lead_in`，把标签紧邻的「本项目/该项目」引导词并入边界
   起点，否则引导词会残留在值尾、值看起来像被截断的坏数据。（VERSION 7→8）

纪律：只做"把原文里已有的东西搬到正确字段"，取不到就留空，绝不编造。
**唯一例外**：``甲方类型`` 是按甲方名称特征词推断的（用户 2026-10-10 要求
"能从招标公告里直接得到或者初步判断的能填就填，减少人工补充信息的时间"），
它的 ``evidence.label`` 固定写「按甲方名称判断」，和原文搬运的字段区分得开。

2026-10-10 追加：提高"公告里其实有、但没填"的字段填充率（VERSION 8→9）
--------------------------------------------------------------------
实测 52 条候选里，原文命中数远大于已填数：联合体 31 vs 9、业绩要求 17 vs 4、
其他要求 11 vs 2、文件售价/报名费 12 vs 4。原因是 ``take()`` 的同义标签太窄
（如 ``业绩要求`` 只认 ``供应商业绩要求``），且不少写法根本不带冒号。
本轮补齐：
* 标签白名单扩容（业绩/其他要求/费用/保证金/联合体/甲方类型）；
* :func:`_joint_venture`：扫「联合体」前后窗口判定 接受/不接受（覆盖 5 种实测写法，
  勾选项 ``☑`` 优先于文字表述）；
* :func:`_file_fee`：``招标文件每套售价0元`` 这类售价句式；
* :func:`_section_tail`：``六、其他补充事宜 无`` 这类**无冒号**章节标签；
* :func:`_money_hit`：投标保证金 / 中标服务费，**必须**数字紧跟 元/万元 才采信
  （实测「投标保证金缴纳的截止时间为：2026-09-24」会被宽松正则抓成金额）。

同一轮还清掉两个存量瑕疵（+VERSION 9→10）：
* :func:`_code_value`：这批公告把编号写在一句话里
  （``项目编号：0730-…/01）招标人为…，招标项目资金已落实。``），
  8 条候选的「项目编号」是 80~97 字整句粘贴；另有个别落成 ``2026-10-09 17:20``
  （发布时间被当编号）、``标包编号/3.``（标签自己）。现按「截句末套话 + 形态校验」
  两步收口，不像编号的判空。
* :func:`_tidy_brackets` / :func:`_truncate_at_sentence`：无条件
  ``strip('（）()')`` 把 ``中化商务有限公司（章）`` 砍成 ``…（章``；
  超长值硬切在半个括号里（``（须提供合同首页、范围页、签``）。现在括号配对就保留、
  超长优先切在句末。

2026-10-10 追加：VERSION 10→11，修「同一 URL 两次抓取排版不同」导致的越界
----------------------------------------------------------------------
VERSION 10 回填时出现回退（项目编号 -3、其他要求 -5、联合体投标 -4，`服务期限`
最长 87→181、`报名费用` 596 字）。诊断根因不是解析器变差，而是**抓回来的正文排版不同**：
四川站同一 URL 第二次抓取时正文被折成一行（3556 字只有 2 个换行），
``HEADING_RE`` / ``GENERIC_LABEL_RE`` 都带 ``(?:^|\n)`` 行首条件，于是全失效，
右边界只剩白名单标签可用 → 越界吞段。

三步修复：
* :func:`TenderDetailsService.refresh_missing` 增加 ``offline=True``：只用已缓存正文
  重算，让「解析器升版到底有没有效」可以**可复现地**判断，而不是被联网抖动淹没；
* 新增中缝标题规则 ``_MID_HEADING_RE`` / ``_MID_SUBHEADING_RE``，兜住被压进正文中间的
  分节标题（``。2.6质量要求：``、``。3.投标人资格要求：``、``3.1一般要求：``）；
* :data:`_FIELD_MAX` 短值字段安全网，挡住再次失守时的超长值。

2026-10-10 追加：VERSION 11→12，把「公告里其实有、但没填」的补上
------------------------------------------------------------------
用户对着「盲投新信息」的详情抽屉指出：项目区域 / 项目类型 / 服务期限 / 报名费用 /
采购方式 / 项目实施地点 / 项目情况简述 全是空的，问「这里面没有能从公告获取的吗」。
逐条核对原文（候选 ``cand_web_c88f46a77474215d91461b5a``，四川省公共资源交易信息网
的政府采购模板，正文 2280 字）后确认，**原文写了但没抽到的有 4 项**，
另有 2 项是「原文确实没写」——后者留空是诚实的，不动。

补的 4 项，根因都是「标签白名单要求 ``标签名：`` 紧跟冒号」，而这套模板不写冒号：
* ``采购方式`` ← ``拟对…项目采用竞争性磋商方式进行采购``（句式，无 ``采购方式：`` 标签）
  → 新增 :func:`_method_value`，扫 ``采用/采取/以…方式`` 句式，用 ``_METHOD_OK_RE``
  白名单归一成标准方式名，取不到留空；
* ``项目情况简述`` ← ``三、采购项目简介\\n本项目为购置消防相关的物资和装备…``
  （无冒号章节）→ 新增 :func:`_section_body`，复用 :func:`_section_tail` 的
  「无冒号章节 + 下一节标题截断」思路；
* ``报名费用`` ← ``（二）本项目竞争性磋商文件免费获取。``（无数值，只有「免费」）
  → :func:`_file_fee` 增加免费句式，落值为 ``免费``；
* ``项目区域`` ← 从 ``甲方名称`` 拆行政区划链（``四川省成都市彭州市濛阳街道…``
  → ``四川省成都市彭州市``）→ 新增 :func:`_region_from_org`。这是**推断值**，
  与 ``甲方类型`` 同类，``evidence.label`` 固定写「按甲方名称判断」。
  实测 ``项目区域`` 是全字段里最空的（34/52），而甲方名称是可用的强信号。

同一轮清掉一个存量脏值：``梅河口市阜康酒精有限责任公司`` 被当成了 ``项目区域``
和 ``项目实施地点``（2 条候选）—— 因为 ``甲方：`` 后面直接跟的就是公司名，
而 :func:`_plausible_location` 只挡「详见/另行/指定」，不挡机构名。
现两个可信度闸都加机构名判据。

顺带收掉一处「废话小字」：``message`` 只在正文被验证墙挡住时才给提示，
``仅读取部分信息，缺失字段留空`` 这种没人看的说明不再下发。

同一轮还补了两个由「联网/缓存两份正文排版不同」暴露出来的洞：
* :func:`_file_fee` 增加**行首标签行**（:data:`_FEE_LABEL_RE`）。四川政采模板把
  ``途径：…获取招标文件`` / ``方式：在线获取`` / ``售价：0元`` 拆成三行，
  而 :data:`_FEE_INLINE_RE` 刻意不跨行 → 6 条公告的 ``报名费用`` 读空。
  同时把「免费」句式的判定挪到数字之后（有真实售价时不被套话顶掉）。
* :meth:`TenderDetailsService.refresh_missing` 增加 **bodyPick**：同一 URL 的
  缓存正文与联网正文都拿到手时，取**抽出字段更多**的那份，而不是默认信联网那份。
  上一轮实测：只在联网那份生效时 ``报名费用`` 掉 2 条 —— 排版抖动不该清掉已读到的事实。
"""
from __future__ import annotations

import html as html_lib
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import Comment, CData, Declaration, Doctype, ProcessingInstruction
from bs4 import BeautifulSoup, NavigableString
from sqlalchemy import select
from app.core.config import settings
from app.repositories.models import Project, SourcePointer

VERSION = 12
TZ = timezone(timedelta(hours=8))
MONEY_LABELS = ('最高投标限价', '最高限价', '投标限价', '采购限价', '招标控制价', '采购控制价',
                '预算总金额', '采购预算金额', '采购预算', '预算金额', '项目预算', '预算',
                '招标金额', '项目金额', '采购金额', '资金金额', '项目资金')
MONEY_NUMBER = r'(?:\d[\d,，]*(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟两]+)'
MONEY_RE = re.compile(
    r'(?P<label>' + '|'.join(sorted(MONEY_LABELS, key=len, reverse=True)) + r')'
    r'\s*(?:[（(]\s*(?P<unit>亿元|万元|元|万|%)\s*[）)])?\s*[：:]?\s*'
    r'(?:为|约为|不超过|不高于|不得超过|不得高于|≤)?\s*'
    r'(?P<value>(?:人民币|RMB|￥|¥)?\s*' + MONEY_NUMBER +
    r'\s*(?:亿元|万元|元|万|%|％)?(?:[/／][^\s，,。；;：:（）()]{1,20})*'
    r'(?:\s*[（(][^（）()\n]{1,80}[）)])?)(?![\d年月日])'
)
BASE_FIELDS = ('项目名称', '甲方名称', '代理机构', '项目区域', '项目类型', '甲方类型', '所属行业',
               '招标金额', '招标时间', '报名截止时间', '开标时间', '服务期限', '报名费用',
               '项目情况简述', '招标公告网址',
               '资质要求', '业绩要求', '基本要求', '其他要求', '联合体投标', '采购方式', '项目编号',
               '投标保证金（元）', '中标服务费（元）', '项目实施地点')
#: 值抽取的标签白名单。2026-10-10 补入政府采购/公共资源交易通用模板的标签
#: （实测「四川省公共资源交易信息网」用「1.采购项目编号 / 3.采购人 / 4.采购代理机构 /
#: 5.所属行业 / 二、资金情况」这套，旧白名单一个都不认，导致甲方名称只有 25% 填充率）。
LABELS = (
    '项目名称', '采购项目名称', '招标项目名称', '标段名称', '标段/包名称',
    '项目编号', '采购项目编号', '招标编号', '采购编号', '标段/包编号', '招标项目编号',
    '项目类型', '服务类型', '采购类别', '公告类型', '项目性质',
    '采购方式', '招标方式', '采购组织形式', '资格审查方式',
    '所属行业', '所属行业分类', '行业分类',
    '项目实施地点', '履约地点', '服务地点', '项目所在地区', '招标项目所在地区',
    '所在地区', '项目区域', '项目地址', '实施地点',
    '甲方名称', '采购人名称', '采购单位', '采购人', '招标人', '业主', '招标单位', '受采购人',
    '代理机构', '招标代理机构', '采购代理机构', '代理单位', '招标代理',
    '项目概况', '项目情况简述', '项目简介', '采购项目简介', '项目内容',
    '采购范围', '招标范围', '采购内容', '服务内容', '服务范围', '规模',
    '其他', '其他补充事宜', '其它补充事宜', '其他补充事宜说明', '其他要求', '其他说明',
    '其他事项', '其他要求说明', '备注',
    '文件获取开始时间', '文件获取截止时间', '文件获取时间', '获取文件时间',
    '报名开始时间', '报名截止时间', '报名截止', '报名时间', '响应截止时间',
    '购标截止时间', '获取采购文件截止时间', '文件获取方式', '获取方式',
    '文件发售金额(元)', '文件发售金额（元）', '采购文件售价', '报名费用', '文件售价',
    '招标文件费用', '获取招标文件费用', '招标文件售价', '采购文件费用', '电子标书费售价',
    '标书费', '标书费售价', '文件费', '工本费', '文件资料费', '平台服务费',
    '文件获取地点', '获取地点',
    '截标/开标时间', '开标时间', '投标截止时间', '响应文件提交截止时间',
    '响应文件递交截止时间和开标时间', '响应文件递交截止时间', '递交截止时间',
    '开标形式', '开标地点', '响应文件递交地点',
    '服务期（天）', '服务期(天)', '服务期限', '服务期说明', '合同履行期限', '服务期',
    '履约期限', '项目服务期', '项目期限',
    '供应商基本要求', '供应商资质要求', '供应商业绩要求', '供应商其他要求',
    '供应商拟投入项目负责人最低要求', '供应商资格要求', '申请人的资格要求', '资格要求',
    '供应商应具备的资格条件', '合格供应商应具备的资格条件', '投标人资格要求',
    '申请人资格要求', '特定资格要求', '资质要求', '业绩要求', '投标人业绩要求',
    '类似业绩要求', '类似业绩', '类似项目业绩', '供应商业绩',
    '是否接受联合体投标', '是否接受联合体参与', '是否接受联合体', '是否允许联合体投标',
    '是否允许联合体', '联合体投标要求', '联合体投标',
    '甲方类型', '采购人类型', '招标人类型', '单位类型', '业主类型',
    '投标保证金', '投标保证金金额', '保证金金额', '投标保证金额',
    '中标服务费', '招标代理服务费', '代理服务费', '中标服务费(元)', '中标服务费（元）',
    '最高限价', '采购预算', '预算金额', '项目预算', '预算', '资金金额', '资金来源',
    '招标金额', '项目金额',
    '联系人', '联系电话', '联系地址', '座机', '手机号码', '通讯地址', '邮编',
    '电子邮箱', '传真', '采购人地址', '代理机构地址', '代理机构联系方式',
    '发布媒介', '公告发布媒介', '注册说明', '平台使用费', '文件下载', 'CA办理',
    '帮助信息', '其他信息', '注意事项', '原文链接', '公告链接',
)
LABEL_RE = re.compile(r'(?:' + '|'.join(re.escape(k) for k in sorted(LABELS, key=len, reverse=True)) + r')\s*[：:]')
SECTION_RE = re.compile(r'项目基本情况|标段/包信息|招标人联系方式|代理机构联系方式|相关说明|时间汇总')
DATE_RE = re.compile(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日)?(?:\s+(\d{1,2})[:：](\d{2}))?')
#: 章节 / 序号标题：值提取的**硬右边界**。旧实现只认 44 个白名单标签，
#: 「一、采购项目基本情况」「2.项目名称」这类标题不被识别 → 值越界吞段。
#: 带句点的序号（``1.`` ``7.2``）允许后面跨空白再接中文，因为公共资源模板里
#: 「1.采购项目编号：」和「1.\n招标条件」两种排版都真实存在；
#: 裸数字（``10 万元``）不在此列 —— 那后面跟中文就成了金额/数量，不是标题。
HEADING_RE = re.compile(
    r'(?:^|\n)[ \t\u3000]*(?P<head>'
    r'[一二三四五六七八九十百]+[、.．]'
    r'|[（(][一二三四五六七八九十]+[）)]'
    r'|\d{1,2}(?:\.\d{1,2})*[、.．)）](?=[ \t\u3000\n]*[\u4e00-\u9fff])'
    r'|\d{1,2}\.\d{1,2}(?=[ \t\u3000]*[\u4e00-\u9fff])'
    r')'
)
#: 通用「短标签：」：行首 2~24 字的冒号标签即可作为右边界（不必进白名单）。
GENERIC_LABEL_RE = re.compile(
    r'(?:^|\n)[ \t\u3000]*[\u4e00-\u9fffA-Za-z0-9（）()\[\]【】／/·\-\.]{2,24}[：:]'
)
#: 「中缝标题」：分节标题被**压进正文中间**时的右边界。实测四川站同一 URL 两次抓取，
#: 第二轮正文被折成一行（3556 字只有 2 个换行）→ 上面两个依赖行首的规则**全部失效**，
#: `服务期限` 从 87 字越界到 181 字、`代理机构` 吃进「开户银行／银行账号」。
#: 两种可判的写法（都要求标题后紧跟冒号，避免误伤「GB/T 1.1标准」这类无冒号引用）：
#:   ① 句末标点之后的 `。2.6质量要求：` / `。3.投标人资格要求：`
#:   ② 句末标点之后的 `。六、缴费注意事项`（中文序号标题常常**不带冒号**）
#:   ③ 行内嵌套的 `3.1一般要求：`（只认 `数字.数字` 形态）
_MID_HEADING_RE = re.compile(
    r'(?<=[。；;！!？?）)\]】\n])[ \t\u3000]*'
    r'(?P<head>'
    r'\d{1,2}(?:\.\d{1,2})*[、.．)）][ \t\u3000]*[\u4e00-\u9fff]{2,14}[：:]'
    r'|\d{1,2}\.\d{1,2}[ \t\u3000]*[\u4e00-\u9fff]{2,14}[：:]'
    r'|[一二三四五六七八九十百]{1,3}[、.．][ \t\u3000]*[\u4e00-\u9fff]{2,16}[：:]?'
    r')'
)
_MID_SUBHEADING_RE = re.compile(
    r'(?<![\d.])'
    r'(?P<head>\d{1,2}\.\d{1,2}[ \t\u3000]*[\u4e00-\u9fff]{2,14}[：:])'
)
#: `采购方式` 的合法取值（含常见同义写法）。不在其中的一律判空 —— 右边界失守时
#: 落进来的接口残渣（``1 SECTION_CODE：I5101…``）比缺失更难被发现。
_METHOD_OK_RE = re.compile(
    r'公开招标|邀请招标|竞争性磋商|竞争性谈判|竞争性比选|单一来源|框架协议|协议供货|'
    r'直接采购|询比采购|询比价|询价|比选|竞标|竞价|议价|谈判|磋商|定点采购|网上竞价|'
    r'电子竞价|公开竞选|邀请询价'
)
#: 少数「短值字段」的硬上限。与 ``_INHERIT_MAX`` 不同，这里连**新算出来的值**也挡：
#: 这些字段的语义决定了值不可能长，一旦超限就是右边界失守（实测 `报名费用` 596 字）。
_FIELD_MAX = {'报名费用': 60, '代理机构': 80, '甲方名称': 80, '项目编号': 60,
              '服务期限': 120, '项目区域': 40, '项目实施地点': 120, '采购方式': 20,
              '项目类型': 20, '甲方类型': 20, '报名截止时间': 24, '开标时间': 24,
              '招标时间': 24, '投标保证金（元）': 40, '中标服务费（元）': 40}
#: 单个字段值的长度上限：超过即截断（原文保留，不编造）。
MAX_VALUE = 600

#: 升版回填时，旧**规则**值允许被继承的长度上限。超过上限说明旧规则把它吞到了
#: 章节/页脚，属于要清掉的垃圾（实测 `代理机构` 1532 字、`服务期限` 2245 字）。
_INHERIT_MAX = {'项目区域': 24, '项目实施地点': 120, '采购方式': 20, '项目类型': 20,
                '甲方名称': 60, '代理机构': 60, '项目编号': 40, '服务期限': 120,
                '报名费用': 30, '报名截止时间': 20, '开标时间': 24, '招标时间': 24}
#: 旧值里出现这些字样 = 吃进了站点链接农场 / 页脚，不予继承。
_INHERIT_NOISE_RE = re.compile(
    r'水利建设市场|国有资产监督管理|发展和改革委|发展改革委|财政厅|公网安备|备案号|访问统计|政府网'
)


def _inherit_ok(key, value):
    """升版回填时，旧规则值是否值得继承（只挡明显的垃圾，不做语义判断）。"""
    text = str(value or '').strip()
    if not text or text in ('/', '-', '—', '无', '暂无'):
        return False
    if len(text) > _INHERIT_MAX.get(key, MAX_VALUE):
        return False
    if _INHERIT_NOISE_RE.search(text):
        return False
    if _looks_like_residue(text):
        return False
    if key in ('报名截止时间', '开标时间', '招标时间') and not re.match(r'^20\d{2}-\d{2}-\d{2}', text):
        return False
    return True

# ======================================================================
# 一、正文提纯：整页 HTML → 可解析的公告纯文本
# ======================================================================

#: 块级标签：前后换行。**行内标签（span/a/b/strong/em/font/u/label…）不换行**，
#: 否则 <p>标签：<span>值</span></p> 会被拆成三行（见模块 docstring 根因 1）。
_BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'br', 'caption', 'dd', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'table', 'tbody', 'td',
    'tfoot', 'th', 'thead', 'tr', 'ul',
})
#: 直接整体删除的标签。**不含 form / select / option / button**：实测两类站点
#: 会把正文放在这些标签里 —— ASP.NET（``HomeInfo/winDidDetails.aspx``）把整页
#: 包在 ``<form runat="server">``；商飞招标站（``csbidding.csair.cn``）正文被解析器
#: 归进了 ``<select>``。删掉它们等于把正文一起删了（实测正文从 3038 字掉到 599、845 字掉到 0）。
_DROP_TAGS = ('script', 'style', 'nav', 'footer', 'noscript', 'iframe', 'svg', 'canvas',
              'link', 'meta')
#: 容器 class/id 命中即删（页脚导航 / 面包屑 / 相关推荐 / 二维码 / 侧栏…）。
_NOISE_ID_RE = re.compile(
    r'footer|foot$|foot[-_]|copyright|friendlink|friendly|sitemap|crumb|breadcrumb|'
    r'pagination|page[-_]nav|share|qrcode|erweima|advert|ads?[-_]|banner|hotword|'
    r'related|recommend|sidebar|side[-_]bar|link[-_]list|bottom[-_]nav|tongji|statistics'
)
#: 正文容器候选，顺序即优先级（具体 → 通用）。
_CONTENT_SELECTORS = (
    '#newsText', '.news-detailed', '.dg-notice-detail', '#noticeContent',
    '.notice-content', '.notice-detail', '.detail-content', '.article-content',
    '.article_content', '.article-con', '.TRS_Editor', '#js_content',
    '.vF_detail_content', '.contentText', '.gg-content', '.news-content',
    '.detail-con', '.detail_content', '#zoom', '.article', 'article',
    '.content-detail', '.detail', '#content', '.content',
)
#: 页脚起始标记：命中即从此行截断（兜住没被容器切干净的站点）。
_FOOTER_MARK_RE = re.compile(
    r'^(?:指导单位|主办单位|承办单位|协办单位|技术支持|运维单位|备案号|网站标识码|'
    r'ICP备案|ICP备|公网安备|网站地图|网站指南|访问统计|RSS订阅|版权|版权所有|公安机关备案)'
)
_CN_NUM = '一二三四五六七八九十百零〇两壹贰叁肆伍陆柒捌玖拾'
#: 数字字符集（不含外层方括号，便于与其它字符一起拼字符类，避免嵌套集合告警）。
_DIGIT_CHARS = '0-9０-９'
_JOIN_TAIL = ('：', ':', '（', '(', '【', '[', '、', '“', '「', '『', '/', '／')


def _drop_noise(soup):
    """删掉脚本 / 样式 / 语义导航，以及 class/id 明确表示噪声的容器。"""
    for node in soup.select(','.join(_DROP_TAGS)):
        node.decompose()
    victims = []
    for node in soup.find_all(True):
        attrs = node.attrs or {}
        ident = ' '.join([str(attrs.get('id') or '')] + [str(c) for c in (attrs.get('class') or [])]).lower()
        if ident and _NOISE_ID_RE.search(ident):
            victims.append(node)
    for node in victims:
        if getattr(node, 'decomposed', False):
            continue
        if any(getattr(parent, 'decomposed', False) for parent in node.parents):
            continue
        node.decompose()


def _content_root(soup):
    """挑正文容器：在候选选择器命中的节点里取**文本最多**的那个。

    2026-10-10 实测教训（第二轮）：按选择器优先级取"第一个命中"会挑小 ——
    同一站点的不同模板里，优先级高的选择器有时只框住内层一小块（实测
    「中国南方航空采购招标网」正文 3038 字被缩到 599 字的页头、
    「中国能建电子采购平台」直接缩到 0 字）。改成取命中节点里最长的那个：
    只会更完整，不会更残缺；越界的页脚由噪声容器剔除 + 页脚截断 + 链接农场截断兜住。
    """
    body = soup.body or soup
    scopes = (body, soup) if body is not soup else (soup,)
    best, best_len = None, 0
    for selector in _CONTENT_SELECTORS:
        for scope in scopes:
            try:
                node = scope.select_one(selector)
            except Exception:  # noqa: BLE001 - 畸形选择器不该中断提纯
                node = None
            if node is None:
                continue
            size = len(node.get_text(' ', strip=True))
            if size >= 120 and size > best_len:
                best, best_len = node, size
            break  # 同一选择器在 body 里只取第一个命中
    if best is not None:
        return best
    best, best_len = body, len(body.get_text(' ', strip=True))
    for node in body.find_all(['div', 'article', 'section', 'td', 'main']):
        size = len(node.get_text(' ', strip=True))
        if size > best_len:
            best, best_len = node, size
    return best


#: 不算正文的字符串节点：HTML 注释 / 声明 / 处理指令。
#: bs4 的 ``get_text`` 本来就跳过它们（``interesting_string_types`` 只含
#: ``NavigableString`` 与 ``CData``），自己遍历 ``descendants`` 时**必须显式排除** ——
#: 否则站点模板里的 JSP 注释会变成正文（实测「内层逻辑：channel.id不等于当前父级栏目id」）。
_NON_TEXT_NODES = (Comment, Declaration, ProcessingInstruction, Doctype)


def _block_text(root):
    """块级标签换行、行内标签不换行的提纯（根因 1 的修法）。"""
    parts = []
    for element in root.descendants:
        if isinstance(element, _NON_TEXT_NODES):
            continue
        if isinstance(element, (NavigableString, CData)):
            parts.append(str(element))
        elif getattr(element, 'name', None) in _BLOCK_TAGS:
            parts.append('\n')
    text = ''.join(parts)
    return text.replace('\u00a0', ' ').replace('\u3000', ' ')


def _should_join(prev, cur):
    """两行是否应粘回一行（被 <span> 切碎的标签 / 序号 / 日期碎片）。"""
    if prev.endswith(_JOIN_TAIL):
        return True
    if re.fullmatch(rf'[{_CN_NUM}]{{1,3}}[、.．]?', prev):
        return True
    if re.fullmatch(rf'[{_DIGIT_CHARS}\s]{{1,4}}', prev) and re.fullmatch(rf'[{_DIGIT_CHARS}\s]{{1,4}}', cur):
        return True
    if re.fullmatch(rf'[{_DIGIT_CHARS}]{{1,4}}', prev) and re.fullmatch(r'[年月日时分秒]', cur):
        return True
    if prev.endswith(('年', '月', '日', '时', '分')) and re.fullmatch(rf'[{_DIGIT_CHARS}]{{1,4}}', cur):
        return True
    # 极短碎片（「采」+「购」+「人：」）：两行都 ≤3 字且上一行没有句读。
    if len(prev) <= 3 and len(cur) <= 3 and not re.search(r'[。；;，,、！？!?]$', prev):
        return True
    return False


def _merge_fragments(lines):
    merged = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if merged and _should_join(merged[-1], line):
            merged[-1] += line
        else:
            merged.append(line)
    return merged


def _cut_footer(lines):
    for index, line in enumerate(lines):
        if _FOOTER_MARK_RE.match(line):
            return lines[:index]
    return lines


def _trim_link_farm(lines):
    """尾部连续 ≥8 行「短且无标点」判定为站点链接农场，整段丢弃。

    只在跑到**文末**时才生效：正文里的短字段表（项目名称 / 值 …）后面还会有
    长句，不会满足"直到底部"这个条件。
    """
    run_start, run = None, 0
    for index, line in enumerate(lines):
        if len(line) <= 12 and not re.search(r'[。；;：，,、！？!?（）()【】\[\]]', line):
            if run == 0:
                run_start = index
            run += 1
        else:
            run_start, run = None, 0
    if run >= 8 and run_start is not None:
        return lines[:run_start]
    return lines


def _unwrap_json_tail(text):
    """兜底：正文里若整段是 JSON 记录（SPA 页面把数据吐在可见文本里），渲染成字段行。"""
    start = text.find('{')
    if start < 0:
        return text
    end = text.rfind('}')
    if end <= start or end - start < 300:
        return text
    try:
        record = json.loads(text[start:end + 1])
    except ValueError:
        return text
    if not isinstance(record, dict):
        return text
    return (text[:start].rstrip('\n') + '\n' + record_text(record)).strip()


def notice_text(html):
    """公告页 HTML → 可解析的纯文本（去脚本样式、锁定正文容器、修被切碎的行）。"""
    soup = BeautifulSoup(html or '', 'html.parser')
    _drop_noise(soup)
    root = _content_root(soup)
    lines = [line.strip() for line in _block_text(root).split('\n')]
    lines = _merge_fragments([line for line in lines if line])
    lines = _trim_link_farm(_cut_footer(lines))
    return _unwrap_json_tail('\n'.join(lines))[:60000]


# ======================================================================
# 二、接口记录 → 可读文本（根因 3）
# ======================================================================

#: 接口字段名 → 中文标签。落不进白名单的键保留原名（原文优先于好看）。
RECORD_KEY_LABELS = {
    'title': '项目名称', 'projectname': '项目名称', 'proj_name': '项目名称',
    'projecttitle': '项目名称', 'bidtitle': '项目名称', 'noticetitle': '项目名称',
    'proj_no': '项目编号', 'projectno': '项目编号', 'projectcode': '项目编号',
    'bidno': '项目编号', 'bid_no': '项目编号', 'noticeno': '项目编号', 'orderno': '项目编号',
    'purchaser': '采购人', 'tenderee': '采购人', 'buyer': '采购人', 'company_name': '采购人',
    'companyname': '采购人', 'clientname': '采购人', 'orgname': '采购人', 'unitname': '采购人',
    'agency': '代理机构', 'agentname': '代理机构', 'agencyname': '代理机构',
    'tendermodal': '采购方式', 'purchasemode': '采购方式', 'purchaser_mode': '采购方式',
    'purhasemode': '采购方式', 'tendermethod': '采购方式', 'tenderway': '采购方式',
    'typename': '公告类型', 'typecode': '公告类型', 'noticetype': '公告类型',
    'areaname': '项目区域', 'area': '项目区域', 'regionname': '项目区域', 'area_code': '项目区域',
    'budget': '预算金额', 'budgetamount': '预算金额', 'budgetmoney': '预算金额',
    'budgettext': '预算金额', 'maximumprice': '最高限价', 'highestlimit': '最高限价',
    'pubtime': '发布时间', 'publishtime': '发布时间', 'publishdate': '发布时间',
    'releasetime': '发布时间', 'date': '发布时间', 'createtime': '发布时间',
    'pointendtime': '报名截止时间', 'endtime': '报名截止时间', 'deadline': '报名截止时间',
    'cng_bid_call_end': '报名截止时间', 'bidcallend': '报名截止时间',
    'registrationendtime': '报名截止时间', 'signupendtime': '报名截止时间',
    'opentime': '开标时间', 'bidopentime': '开标时间', 'cng_bid_open_time': '开标时间',
    'text': '正文', 'content': '正文', 'detail': '正文', 'detailcontent': '正文',
    'noticecontent': '正文', 'body': '正文', 'comment': '正文', 'memo': '备注',
    'remark': '备注', 'summary': '项目概况', 'description': '项目概况',
    'linkurl': '原文链接', 'href': '原文链接', 'url': '原文链接', 'link': '原文链接',
}
#: 接口字段名 → 中文标签里明确是噪声的直接丢（id / 审计列 / 图片 / 排序）。
RECORD_SKIP_KEYS = frozenset({
    'id', 'ids', 'uuid', 'guid', 'creatorid', 'createrid', 'modifierid', 'tenantid',
    'deleted', 'deleteflag', 'sort', 'order', 'pictureid', 'pictureurl', 'imgurl',
    'menucode', 'platformid', 'subjectid', 'publishstatus', 'modifytime', 'updatetime',
    'createuser', 'updateuser', 'orgid', 'sourceid', 'categoryid', 'categorycode',
    'mckeys_raw', 'pageindex', 'pagesize', 'totalcount', 'total', 'success', 'code', 'msg',
})
#: 记录里的 XML 元数据（建易采实测：<CNG_BID_CALL_END value="20261018180000"/>）。
_XML_META_RE = re.compile(r'<([A-Za-z_][\w.\-]*)\s+value="([^"]*)"\s*/?>')
_TS14_RE = re.compile(r'^(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$')


def _record_scalar(name, raw):
    """把接口里的标量渲染成人类可读文本；时间戳还原成时间。"""
    text = str(raw).strip()
    if text.isdigit():
        number = int(text)
        if 10**12 <= number <= 10**13:          # 毫秒时间戳
            try:
                return datetime.fromtimestamp(number / 1000, TZ).strftime('%Y-%m-%d %H:%M')
            except (OverflowError, OSError, ValueError):
                return text
        match = _TS14_RE.match(text)            # 20261018180000
        if match and 1 <= int(match[2]) <= 12 and 1 <= int(match[3]) <= 31:
            return '%s-%s-%s %s:%s' % match.groups()[:5]
        if 10**9 <= number <= 10**10:           # 秒时间戳
            try:
                return datetime.fromtimestamp(number, TZ).strftime('%Y-%m-%d %H:%M')
            except (OverflowError, OSError, ValueError):
                return text
    if re.search(r'[Tt]\d{2}:\d{2}:\d{2}', text):
        return text.replace('T', ' ')[:16]
    return re.sub(r'\s+', ' ', text).strip()


def record_text(record):
    """接口记录 → ``字段：值`` 可读文本；内含 HTML 的字段走正文提纯，XML 元数据展开。"""
    if not isinstance(record, dict):
        return ''
    lines, blocks = [], []
    for key, value in record.items():
        name = str(key)
        if name.lower() in RECORD_SKIP_KEYS or value is None or value is False or value == '':
            continue
        raw = html_lib.unescape(str(value)).strip()
        if not raw:
            continue
        metas = _XML_META_RE.findall(raw)
        if len(metas) >= 2:
            for meta_key, meta_value in metas:
                text = _record_scalar(meta_key, meta_value)
                if text:
                    lines.append(f'{RECORD_KEY_LABELS.get(meta_key.lower(), meta_key)}：{text}')
            continue
        if re.search(r'<[A-Za-z][^>]*>', raw):
            extracted = notice_text(raw).strip()
            if extracted:
                blocks.append(extracted)
            continue
        text = _record_scalar(name, raw)
        if not text:
            continue
        if len(text) > 300:
            blocks.append(text[:4000])
        else:
            lines.append(f'{RECORD_KEY_LABELS.get(name.lower(), name)}：{text}')
    head = '\n'.join(lines)
    if blocks:
        head = (head + '\n' if head else '') + '\n'.join(blocks)
    return head[:20000]


# ======================================================================
# 三、字段抽取
# ======================================================================
def public_amount(text, mapped):
    """公开金额必填：保留预算、限价等原口径；缺单位也保留数额，不猜单位。"""
    amounts, evidence, seen = [], [], set()
    for match in MONEY_RE.finditer(text or ''):
        label, value, unit = match['label'], match['value'].strip(), match['unit']
        value = re.sub(r'\s+', ' ', value)
        if not unit and not re.search(r'元|万|亿|%|％', value) and re.match(r'\s*[年月日]', (text or '')[match.end():]):
            continue
        if unit and re.fullmatch(r'(?:人民币|RMB|￥|¥)?\s*' + MONEY_NUMBER, value):
            value += unit
        # 标包金额全部保留，并携带紧邻的标包名称；文件费不在金额标签范围内。
        line_start = (text or '').rfind('\n', 0, match.start()) + 1
        prefix = (text or '')[line_start:match.start()].strip()
        if len(prefix) <= 40 and re.search(r'采购包|标包|标段|包号', prefix):
            label = prefix + label
        key = (label, re.sub(r'\s+', '', value))
        if key in seen:
            continue
        seen.add(key)
        amounts.append(f'{label}：{value}')
        evidence.append({'label': label, 'text': match.group()})
    # 接口的结构化公开金额也可用；没有明示单位时不进行金额换算。
    if not amounts:
        for key, label in (('budget_text', '采购预算'), ('maximum_price', '最高限价'),
                           ('highest_limit', '最高限价'), ('budget_amount', '采购预算'),
                           ('budget', '采购预算'), ('招标金额', '招标金额')):
            raw = mapped.get(key)
            if raw is None:
                continue
            value = str(raw).strip()
            if not re.search(MONEY_NUMBER, value) or re.search(r'\*{2}|登录后|点击登录|登录查看', value):
                continue
            amounts.append(f'{label}：{value}')
            evidence.append({'label': key, 'text': value})
    return '\n'.join(amounts), evidence


def notice_key(title):
    value = unicodedata.normalize('NFKC', title or '')
    value = re.sub(r'^\s*\[[^\]]+\]\s*', '', value)
    value = re.sub(r'(?:询比采购|竞争性磋商|竞争性谈判|公开招标|招标|采购|比选|询价)(?:公告|通知)$', '', value)
    return re.sub(r'[\W_]', '', value).lower()


def notice_matches(title, text, head=4000):
    """正文是不是这条公告的正文（补取时防止把别的页面套到本项目上）。

    比旧口径（整个标题核心必须原样出现）宽一档：列表页标题常被站点加工过
    （``[公开招标] 某某项目 公开招标 2026-10-08`` —— 前缀、类型、日期都是列表页拼的），
    详情页只会出现中间的项名。用标题核心的**前 8 字**做锚点即可，
    既跟得上站点拼装，又不至于把无关页面认成同一项目。
    """
    core = notice_key(title)
    if len(core) < 6:
        return True
    haystack = notice_key((text or '')[:head])
    return core in haystack or core[:8] in haystack


def date_value(raw):
    match = DATE_RE.search(raw or '')
    if not match:
        return ''
    y, m, d, hh, mm = match.groups()
    try:
        date = datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0))
    except ValueError:
        return ''
    return date.strftime('%Y-%m-%d %H:%M' if hh is not None else '%Y-%m-%d')


def blocked_text(text):
    return len(text or '') < 2000 and bool(re.search(r'人机验证|安全验证|验证码|访问过于频繁|登录后查看|点击登录查看', text or ''))


#: 地点类标签的优先级顺序（越靠前越贴项目本身）。
_LOCATION_KEYS = ('项目实施地点', '实施地点', '项目地址', '履约地点', '服务地点',
                  '项目所在地区', '招标项目所在地区', '所在地区')


#: 机构名判据。地点类字段最常见的脏值就是「甲方名称」或「代理机构」被原样搬进来：
#: 实测 ``梅河口市阜康酒精有限责任公司`` 同时成了 ``项目区域`` 和 ``项目实施地点``
#: （2 条候选），因为原文的 ``甲方：`` 后面直接跟的就是公司名。
_ORG_NAME_RE = re.compile(
    r'有限责任公司|股份有限公司|有限公司|公司|集团|厂|商行|合作社|事务所|'
    r'研究院|设计院|事务所|中心$|医院$|学校$|大学$'
)


def _plausible_region(value):
    """项目区域兜底值的可信度闸：过长、带句读、或写明"另行通知"的一律不留。"""
    value = (value or '').strip(' 。．.;；,，、:：')
    if not value or len(value) > 24:
        return ''
    if re.search(r'[。；;]|指定|另行|详见|按甲方|根据甲方|待定|甲方要求|自定', value):
        return ''
    if _ORG_NAME_RE.search(value):
        return ''
    return value


def _plausible_location(value):
    """地点值的可信度闸：排除「/」「根据甲方要求 / 详见文件」这类不是地点的填充语。

    通过两条口径之一即认：① 含地理后缀（省市区县镇乡街道路号园区大厦广场机场…）；
    ② 短且整串都是中文/数字/连字符（实测 `陕西西安`、`吉林`、`西南科技大学`
    这类公告原文就是不带"省/市"后缀的写法）。带句读、带动作主语（投标人/供应商）
    或带"指定/详见/另行"的一律不认。机构名也不认（见 :data:`_ORG_NAME_RE`）。
    """
    value = re.sub(r'[（(][^（）()]{0,200}$', '', (value or '').strip()).strip()
    value = value.strip(' 。．.;；,，、:：')
    if not value or len(value) > 120 or len(value) < 2:
        return False
    if re.search(r'[。；;]|根据甲方|按甲方|甲方|指定|详见|待定|自定|另行|为准|投标人|供应商|招标人|[、/]', value):
        return False
    if _ORG_NAME_RE.search(value):
        return False
    if len(value) <= 20 and re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9\-]+', value):
        return True
    return bool(re.search(r'省|市|区|县|州|镇|乡|街道|路|街|号|园区|大厦|广场|地段|机场|以内|内$', value))


#: 行政区划链的终点：省 / 市 / 区 / 县 / 州 / 盟 / 旗。用于把地点串和机构名的
#: 前缀裁成「省市区」这一截（``四川省成都市彭州市濛阳`` → ``四川省成都市彭州市``）。
_REGION_TAIL_CHARS = '省市区县州盟旗'
#: 行政区划链的截断词：碰到就停，后面的都是具体地址或机构名自己的字。
_REGION_CUT_RE = re.compile(
    r'街道|镇|乡|社区|村|委员会|居委会|管委会|公司|集团|厂|局|院|所|学校|大学|医院|'
    r'中心|办公室|指挥部|项目部|基地|园区|大厦|广场'
)


def _region_from_org(name):
    """从机构名/地址串里拆出「省市区」这一截；拆不出返回空（不硬猜）。

    只做字符串裁剪，不做地名库校验 —— 判据是「有行政区划后缀」这个硬信号：
    ``四川省成都市彭州市濛阳街道义和社区居民委员会`` → ``四川省成都市彭州市``；
    ``梅河口市阜康酒精有限责任公司`` → ``梅河口市``；
    ``中技国际招标有限公司`` → ``（空）``（裁完没有行政区划后缀）。
    """
    text = re.sub(r'\s+', '', name or '')
    if not text:
        return ''
    prefix = _REGION_CUT_RE.split(text, 1)[0]
    while prefix and prefix[-1] not in _REGION_TAIL_CHARS:
        prefix = prefix[:-1]
    return _plausible_region(prefix)


#: 机构名后面紧跟的联系方式子标签。这些子标签在原文里常写成「地 址」（字间带空格），
#: 与 ``LABEL_RE`` 的「标签名后紧跟冒号」口径不匹配 → 整个联系方式尾巴被吞进机构名。
#: 实测「代理机构」字段吃进「地 址：… 联 系 人：… 电 话：…」共 65~93 字。
_CONTACT_TAIL_RE = re.compile(
    r'\s*(?:地\s*址|通\s*讯\s*地\s*址|注\s*册\s*地\s*址|'
    r'联\s*系\s*人|联\s*系\s*电\s*话|联系电话|电\s*话|传\s*真|'
    r'电子邮箱|邮\s*箱|邮\s*编|网\s*址|联系方式|'
    r'开户银行|开\s*户\s*行|银行账号|银\s*行\s*账\s*号|银行账户|账\s*号|账户名称|'
    r'纳税人识别号|统一社会信用代码|法定代表\s*人|负\s*责\s*人)\s*[:：]'
)
# 注意：**不要**在这里加「公\s*司\s*地\s*址」「单\s*位\s*地\s*址」——
# ``有限公司 地址：`` 会被它整段匹配，机构名被砍成 ``…有限``（实测）。
# 「地址」这个子标签本身已足以定位尾巴。
#: 机构名的「有效内容」判据：去掉地址/联系人等子标签字样后还剩什么。
_ORG_STOPWORD_RE = re.compile(
    r'地\s*址|通\s*讯\s*地\s*址|联\s*系\s*人|联\s*系\s*电\s*话|联系电话|电\s*话|传\s*真|'
    r'电子邮箱|邮\s*箱|邮\s*编|网\s*址|联系方式|开户银行|银行账号|账\s*号|'
    r'纳税人识别号|统一社会信用代码|法定代表\s*人|负\s*责\s*人|[:：]'
)


def _org_value(value):
    """机构名清洗：切掉紧跟的地址/联系人/电话尾巴，并挡掉纯标签占位垃圾。

    只做两件事，不做语义判断：
    ① 在第一个联系方式子标签处截断（``XX招标有限公司 地 址：… 联 系 人：…`` → ``XX招标有限公司``）；
    ② 去掉子标签字样后若什么都不剩（实测模板占位 ``代理机构：地址:地址:``），判为垃圾返回空，
       让 ``take`` 继续试下一个同义标签，而不是把一个假值落库。
    """
    text = re.sub(r'\s+', ' ', value or '').strip()
    if not text:
        return ''
    text = _CONTACT_TAIL_RE.split(text)[0]
    text = re.sub(r'[\s；;。，,、：:]+$', '', text).strip()
    # 截掉联系方式尾巴后，剩下的如果只是站点访问门槛，同样判垃圾 ——
    # 实测 ``招标单位=正式会员“登录”后可浏览】 联 系 人： …``，截断后残留
    # ``正式会员“登录”后可浏览】``，被当成甲方名称落库。
    if _looks_like_residue(text):
        return ''
    # 右边界落在下一句的词中间时会留一个孤立残词（实测 ``四川省盐业集团自贡有限公司采购``）：
    # 机构名以「公司/局/院/厂/所/中心…」结尾后，不可能再跟一个动词性残片。
    text = re.sub(r'(?<=[公司局院厂所中心部委办处站队库行馆园])(?:采购|招标|投标|项目|工程)$', '', text).strip()
    if not text or not _ORG_STOPWORD_RE.sub('', text).strip():
        return ''
    if len(text) > 60:
        text = text[:60].rstrip()
    return text


#: 站点「访问门槛」文案：被当成字段值时一律判空（实测 ``正式会员“登录”后可浏览``）。
#: 判据刻意保守 —— 「短 + 提到登录/会员/注册 + 提到浏览/查看/可见 + 没有句读」四者同时成立
#: 才算门槛文案，否则会把「投标人需登录平台获取招标文件，…」这类正常说明一起清掉。
_RESIDUE_SUBJECT_RE = re.compile(r'登录|会员|注册')
_RESIDUE_ACTION_RE = re.compile(r'浏览|查看|可见|访问|下载|阅读')


def _looks_like_residue(value):
    if len(value) > 30 or re.search(r'[，。；;]', value):
        return False
    return bool(_RESIDUE_SUBJECT_RE.search(value) and _RESIDUE_ACTION_RE.search(value))


def _clean_value(value):
    """值清洗：剥 HTML 残留 / 实体、去掉头尾引号括号序号、限长。"""
    value = re.sub(r'<[^>]{1,300}>', '', value or '')
    value = html_lib.unescape(value)
    value = re.sub(r'\s+', ' ', value).strip()
    value = value.strip(' \t【】[]“”"\'')
    # 站点访问门槛被当成值：实测 ``代理机构=正式会员“登录”后可浏览``、
    # ``甲方名称=正式会员“登录”后可浏览】``。判据见 :func:`_looks_like_residue`。
    if _looks_like_residue(value):
        return ''
    value = _tidy_brackets(value)
    value = re.sub(r'^[：:、.．\-\s]+', '', value)
    # 只剩一个标包前缀、冒号后面什么都没有的值不是「值」（实测 服务期限=「采购包1：」），
    # 落库只会让人以为是抓坏了 —— 判为空，交给下一个同义标签或留空。
    if re.fullmatch(r'(?:采购包|标包|标段|包号)\s*[\d一二三四五六七八九十]+\s*[：:]?', value):
        return ''
    value = re.sub(r'[\s；;。，,、]+$', '', value)
    # 右边界落在「2.项目名称：」中间时，值尾部会粘着序号残片（实测 项目编号=「SC0116… 2.」）。
    value = re.sub(r'(?:(?<=[\u4e00-\u9fff\s])|\A)\d{1,2}(?:\.\d{1,2})*[、.．)）]\s*$', '', value)
    if len(value) > MAX_VALUE:
        return _truncate_at_sentence(value).strip()
    return value.strip()


def _tidy_brackets(value):
    """圆括号只在**不配对**时才剥掉。

    实测 ``代理机构=「中化商务有限公司（章」``、``报名费用=「人民币200元/份（…不能转让」``：
    原文括号是配对的，是无条件 ``strip('（）()')`` 把末尾的右括号砍掉了，
    值看上去像抓坏了。配对完整就原样保留。
    """
    text = value or ''
    for opener, closer in (('（', '）'), ('(', ')')):
        if text.count(opener) != text.count(closer):
            return text.strip('（）()')
    return text


def _truncate_at_sentence(value, limit=MAX_VALUE, first_sentence=False):
    """超长时优先在句末（。；;）截断，别停在半个括号里。

    实测 ``业绩要求`` 到 600 字上限时被硬切在「（须提供合同首页、范围页、签」——
    读的人无法判断是抓坏了还是原文就这么长。

    ``first_sentence=True`` 用于被 :data:`_FIELD_MAX` 限死的「短值字段」：
    这些字段的值本来就该是第一句话，取**第一个**句末比取最后一个更接近原意
    （实测 ``报名费用`` 被硬切在「…善建云采（四川华西集团有限公司及下属分子公司」）。
    """
    head = (value or '')[:limit]
    if first_sentence:
        cut = min([pos for pos in (head.find('。'), head.find('；'), head.find(';'), head.find('\n'))
                   if pos >= 0] or [-1])
        if cut >= 0:
            return head[:cut + 1]
        return head.rstrip()
    cut = max(head.rfind('。'), head.rfind('；'), head.rfind(';'), head.rfind('\n'))
    if cut >= int(limit * 0.6):
        return head[:cut + 1]
    head = head.rstrip()
    # 连句末都找不到只能硬切时，别停在半个括号里（实测 ``资质要求`` 尾部落成「（」）。
    if head.count('（') > head.count('）'):
        pos = head.rfind('（')
        if pos > 0:
            head = head[:pos].rstrip(' ；;。，,、')
    return head


#: 标签前的引导词。原文常写「本项目是否接受联合体参与：」「该项目预算金额：」；
#: 若边界只从「是否接受…」起算，引导词「本项目」会残留在**上一个值的尾部**
#: （实测 `服务期限` 尾巴多出「本项目」，值看起来像被截断的坏数据）。
#: 因此在登记边界时把紧邻的引导词一并算进去 —— 只认「本/该/此 … 项目」这种
#: 明确句式，且到标签之间不许有句读，避免把正文当成引导词吃掉。
_LEAD_IN_RE = re.compile(r'(?:本|该|此)[\u4e00-\u9fff]{0,6}项目[ \t\u3000]*$')


def _with_lead_in(text, start):
    """把标签紧邻的「本项目 / 该项目」引导词并入边界起点（没有则原样返回）。"""
    window_start = max(0, (start or 0) - 12)
    match = _LEAD_IN_RE.search((text or '')[window_start:start])
    return window_start + match.start() if match else start


def _boundaries(text):
    """值提取的右边界集合：白名单标签 + 章节标题 + 通用冒号标签 + 已识别章节。

    **只收标题的起点，不收终点** —— 收终点会把紧跟在标题后的正文一起切掉
    （实测「二、申请人的资格要求：1.具有独立承担民事责任的能力」被切成了「1.」）。
    """
    cuts = set()
    for match in LABEL_RE.finditer(text or ''):
        if match.group().rstrip('：:').strip() in LABELS:
            cuts.add(_with_lead_in(text, match.start()))
    for match in SECTION_RE.finditer(text or ''):
        cuts.add(_with_lead_in(text, match.start()))
    for match in HEADING_RE.finditer(text or ''):
        cuts.add(match.start())
    for match in GENERIC_LABEL_RE.finditer(text or ''):
        cuts.add(_with_lead_in(text, match.start()))
    # 行首规则在「正文被压成一行」的页面上全部失效，用中缝标题兜住右边界。
    for match in _MID_HEADING_RE.finditer(text or ''):
        cuts.add(match.start('head'))
    for match in _MID_SUBHEADING_RE.finditer(text or ''):
        cuts.add(match.start('head'))
    return sorted(cuts)


def _value_end(text, start, cuts):
    """取值的右边界 = 最近的一个边界。

    例外：边界紧贴标签且中间**只有换行/序号、没有中文**时（``标签：\\n1.正文…``），
    该序号是值的列表编号，不是下一个字段 —— 此时往下找一个真边界。
    判据是"该序号处能不能匹配到下一个「短标签：」"：能匹配就是下一个字段（不跳），
    匹配不到就把编号列表整段当作值（跳）。
    """
    for cut in cuts:
        if cut <= start:
            continue
        gap = text[start:cut]
        if len(gap) <= 4 and not re.search(r'[\u4e00-\u9fff]', gap):
            if GENERIC_LABEL_RE.match(text, cut):
                return cut
            continue
        return cut
    return len(text)


def _doc_deadline(text):
    """报名截止时间的兜底：从「获取/下载文件」时间区间里取 ``至 <日期>``。

    只在报名/获取文件语境下采信（区间前后 200 字内必须同时出现动作词与"文件"），
    且 evidence 标 ``文件获取截止``，让人一眼看出这不是公告明写的报名截止。
    """
    for match in re.finditer(r'至\s*(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2})', text or ''):
        window = (text or '')[max(0, match.start() - 200):match.end() + 200]
        if re.search(r'获取|领取|申领|下载|购买|报名', window) and '文件' in window:
            return match.group(1)
    return ''


def _plausible_link(value):
    match = re.search(r'https?://[^\s，,。；;）)】"\'<>]+', value or '')
    if not match:
        return ''
    link = match.group().rstrip('.,;')
    host = urlparse(link).netloc
    return link if host and '.' in host else ''


def _is_channel_home(url):
    """判断存下来的链接是不是频道首页（这类链接对用户点开没意义）。"""
    try:
        parsed = urlparse(url or '')
    except ValueError:
        return False
    return parsed.path in ('', '/') and not parsed.query


# ======================================================================
# 一·五、公告原文「直接可得」或「可初步判断」的补充字段
# ======================================================================
# 用户 2026-10-10 追加要求：「能从招标公告里直接得到或者初步判断的能填就填，
# 减少人工补充信息的时间」。这批取值分两类，``evidence.label`` 会写清来源，
# 读的人一眼能分辨是"原文原话"还是"推断"：
#   * **原文可得**：联合体投标 / 业绩要求 / 其他要求 / 报名费用 / 投标保证金 /
#     中标服务费 —— 原文都有，只是写法比标签白名单散得多，靠句式兜住；
#   * **初步判断**：目前只有 `甲方类型`，按甲方名称的机构特征词推断，
#     evidence.label 固定写「按甲方名称判断」，**明确标注这不是原文原话**。

#: 「联合体」的判据。原文写法实测至少 5 种：
#:   ``本次招标不接受联合体投标。`` ／ ``本项目不允许联合体参与磋商。``
#:   ``本项目不允许联合体投标。`` ／ ``(□接受、☑不接受)联合体参加采购活动``
#:   ``本项目是否接受联合体投标： 采购包1：不接受联合体投标``
#: 标签白名单只覆盖最后一种，其余四种都靠下面的窗口判据。
#: **勾选项优先**：``(☑接受、□不接受)`` 与 ``(□接受、☑不接受)`` 两个方向都存在，
#: 先看勾选符号，再退回文字表述，避免「谁在前谁赢」。
_JV_TICK_NEG_RE = re.compile(r'(?:☑|√|■|⊠)\s*不')
_JV_TICK_POS_RE = re.compile(r'(?:☑|√|■|⊠)\s*接受')
_JV_WORD_NEG_RE = re.compile(r'不(?:接受|允许|组织)|禁止|拒绝|谢绝')
_JV_WORD_POS_RE = re.compile(r'(?:接受|允许)\s*联合体')


def _joint_venture(text):
    """是否接受联合体投标 → ``接受`` / ``不接受``（取原文第一次明确表态）。"""
    for match in re.finditer(r'联合体', text or ''):
        window = (text or '')[max(0, match.start() - 40):match.end() + 12]
        if _JV_TICK_NEG_RE.search(window):
            return '不接受'
        if _JV_TICK_POS_RE.search(window):
            return '接受'
        if _JV_WORD_NEG_RE.search(window):
            return '不接受'
        if _JV_WORD_POS_RE.search(window):
            return '接受'
    return ''


#: 文件/报名费用句式。实测写法：``招标文件每套售价0元。``
#: ``获取招标文件费用：0元/项目`` ``招标文件费用500元`` ``电子标书费售价：人民币40 元/套``
_FEE_INLINE_RE = re.compile(
    r'(?:招标文件|采购文件|磋商文件|谈判文件|询价文件|电子标书|标书|文件)'
    r'[^。；\n]{0,12}?(?:每套|每份)?\s*(?:售价|费用|金额|工本费|工本)?\s*[:：]?\s*'
    r'(?:为|人民币)?\s*([\d,，]{1,9}(?:\.\d+)?)\s*元(?:\s*/\s*(套|份|项目|包|标段))?'
)


#: 免费领取句式。实测：``（二）本项目竞争性磋商文件免费获取。``
#: ``采购文件不收取费用`` ``招标文件免费下载`` —— 这类写法**没有数值**，
#: 旧的 ``_FEE_INLINE_RE`` 要求 ``数字 + 元``，一个都抓不到。
_FREE_FEE_RE = re.compile(
    r'(?:招标文件|采购文件|磋商文件|谈判文件|询价文件|电子标书|标书|文件)'
    r'[^。；\n]{0,12}?(?:免费|不收取费用|不收费|无偿)'
)

#: **独立成行**的费用标签。实测四川政采模板把费用拆成三行：
#: ``途径：…未获取采购文件中选择本项目获取招标文件`` / ``方式：在线获取`` /
#: ``售价：0元`` —— ``售价`` 与「文件」之间隔着换行，而 :data:`_FEE_INLINE_RE`
#: 的 ``[^。；\n]{0,12}?`` **不允许跨行**（当初刻意排除 ``\n`` 就是为了不把
#: ``获取招标文件`` 和下一行的 ``限价：100,000元`` 连起来当费用），于是 6 条
#: 四川政采公告的 ``报名费用`` 全部读空。
#: 这里专门认「行首标签行 + 价格/免费」，既不跨行也不会误吃别的金额。
_FEE_LABEL_RE = re.compile(
    r'(?:^|\n|[。；;])\s*'
    r'(?:售价|标书售价|文件售价|标书费|文件费|工本费|报名费|资料费|平台服务费)\s*[:：]\s*'
    r'(免费|不收取费用|不收费|无偿|'
    r'(?:\d[\d,，]*(?:\.\d+)?|[零〇一二三四五六七八九十百千万]+)\s*(?:万元|元)'
    r'(?:\s*/\s*(?:套|份|项目|包|标段))?)'
)


def _file_fee(text):
    """报名/文件费用：取原文售价句式（标签白名单之外的写法）。取不到留空。

    **顺序即优先级**：行首标签行 → 同一行内的售价句式 → 免费句式。
    数值优先于「免费」—— 实测有的公告既有「…文件免费获取」的套话，
    又真的写了售价，先认数字不会把真实金额丢掉。
    """
    text = text or ''
    label = _FEE_LABEL_RE.search(text)
    if label:
        value = re.sub(r'\s+', '', label.group(1))
        return '免费' if re.search(r'免费|不收取|不收费|无偿', value) else value
    for match in _FEE_INLINE_RE.finditer(text):
        number = match.group(1).replace(',', '').replace('，', '')
        unit = match.group(2)
        return f'{number}元' + (f'/{unit}' if unit else '')
    if _FREE_FEE_RE.search(text):
        return '免费'
    return ''


#: 采购方式的句式写法。这套政府采购模板不写 ``采购方式：公开招标``，
#: 而是把方式织进第一句话里：``拟对…项目采用竞争性磋商方式进行采购，
#: 特邀请符合本次采购要求的供应商参加本项目的竞争性磋商。``
#: 实测 ``采购方式`` 字段因此常年为空（23/52）。
#: 只扫 ``采用/采取/拟采用/以…方式`` 这一种句式，命中后再用 ``_METHOD_OK_RE``
#: 归一成标准方式名（``竞争性磋商``），原文用词不规范就判空。
_METHOD_SENTENCE_RE = re.compile(
    r'(?:采用|采取|拟采用|确定采用|选用|以)\s*'
    r'(?P<method>[^\s，。；、（）()]{2,20}?)\s*(?:方式|办法|形式)'
)


def _method_value(text):
    """采购方式：扫「采用…方式」句式，取第一个能对上方式白名单的。

    句式里的中缀（``采用 X 的方式``）允许，但**带否定修饰的不采信**：
    实测「采取现场**不公开**竞标方式」会被抓成 ``竞标``，丢掉了「不公开」这个
    决定性限定词 —— 而那家公告自己的表格里写的是「招标方式：竞争性谈判」。
    输出一个少了限定词的方式名比留空更容易误导人，所以带 ``不/未/无`` 的一律跳过。
    """
    for match in _METHOD_SENTENCE_RE.finditer(text or ''):
        phrase = match.group('method')
        hit = _METHOD_OK_RE.search(phrase)
        if not hit:
            continue
        if re.search(r'[不未无]', phrase[:hit.start()] + phrase[hit.end():]):
            continue
        return hit.group()
    return ''


#: 无冒号章节式的「项目简介」。实测 ``三、采购项目简介`` 后面换行接正文，
#: 整个章节标题**没有冒号**，``LABEL_RE`` 匹配不到 → ``项目情况简述`` 空（14/52）。
#:
#: **标签只留真正的"简介类"**。``采购内容 / 采购范围 / 招标范围 / 项目内容``
#: 看着更贴题，实测全是误命中：``2.项目概况与招标范围``（章节标题自己）、
#: ``二、采购内容`` 后面跟的是表头 ``项目名称采购预算（元/人.月）成交公司数量（名）``、
#: ``与本项目采购内容无关或已结束``（句子中间）。加它们净收益为负。
_SECTION_BODY_RE = re.compile(
    r'(?:采购项目简介|项目简介|项目概况|项目综述|项目描述)\s*[:：]?\s*'
)
#: 「标题位」判据：触发词必须处在标题位置 —— 行首、句末标点之后，或章节序号
#: （``三、`` ``2.`` ``2.1``）之后。句子中间的词（``与本项目采购内容无关``）挡在门外。
_SECTION_BODY_HEAD_RE = re.compile(
    r'(?:^|\n|[。；;]|[一二三四五六七八九十百]{1,3}[、.．]|\d{1,2}(?:\.\d{1,2})*[、.．)）])\s*$'
)
#: 章节正文里不该出现的内容。政府采购公告的「项目概况」章节其实是**流程套话**
#: （``XXX项目的潜在投标人应在四川省政府采购一体化平台…获取招标文件，
#: 并于…递交投标文件``）—— 那是导航块，落进「项目情况简述」比留空更糟（实测 12 条）。
_SECTION_BODY_NOISE_RE = re.compile(
    r'潜在投标人|潜在供应商|潜在申请人|获取招标文件|获取采购文件|获取磋商文件|'
    r'递交投标文件|提交响应文件|递交响应文件|电子化交易系统'
)
#: 触发词紧跟着这些字，说明它只是某个更长标题的一部分（``项目概况与招标范围``、
#: ``采购内容和范围``），不是章节标题。
_SECTION_BODY_TAIL_CHARS = ('与', '和', '及', '、', ',', '，')


def _section_body(text, cuts):
    """取「采购项目简介」这类无冒号章节后面的内容（到下一个边界为止）。

    与 :func:`_section_tail` 同一套截断手法：先用 ``_value_end`` 找到最近边界，
    再额外用中文数字序号标题兜一次 —— 正文被压成一行时章节标题不在行首，
    只有序号能定位（``…详见采购清单。四、供应商邀请方式``）。

    逐个候选位置往下试，第一个通过全部闸门的才算数；全不过就返回空。
    """
    raw = text or ''
    for match in _SECTION_BODY_RE.finditer(raw):
        # 闸 1：必须处在标题位（行首 / 句末 / 章节序号之后）
        if not _SECTION_BODY_HEAD_RE.search(raw, 0, match.start()):
            continue
        # 闸 2：后面不能直接接「与/和/及」——说明是更长标题的一部分
        if raw[match.end():match.end() + 1] in _SECTION_BODY_TAIL_CHARS:
            continue
        end = _value_end(raw, match.end(), cuts)
        heading = _TAIL_HEADING_RE.search(raw, match.end())
        if heading and match.end() < heading.start(1) < end:
            end = heading.start(1)
        value = _clean_value(raw[match.end():end])
        # 闸 3：值要有实质内容（实测 HTML 残渣正文会落出 ``1.1`` 这种碎片）
        if len(value) < 8 or not re.search(r'[\u4e00-\u9fff]{2,}', value):
            continue
        # 闸 4：流程套话不是项目简介
        if _SECTION_BODY_NOISE_RE.search(value):
            continue
        return value
    return ''


#: 保证金 / 代理服务费：**必须**金额后面直接跟 元/万元，才对得上原文。
#: 实测「投标保证金缴纳的截止时间为：2026-09-24 10:00」这种句式会被
#: 宽松正则抓成金额，所以这里要求紧跟单位，宁可取不到。
_DEPOSIT_RE = re.compile(
    r'(?:投标|响应)?保证金[^。；\n]{0,16}?(?:金额|数额|为|是)?\s*[:：]?\s*'
    r'(?:人民币)?\s*([\d,，]{1,12}(?:\.\d+)?)\s*(万元|元)'
)
_AGENT_FEE_RE = re.compile(
    r'(?:中标服务费|招标代理服务费|代理服务费|招标代理费|中标服务)[^。；\n]{0,16}?(?:金额|为|是)?\s*[:：]?\s*'
    r'(?:人民币)?\s*([\d,，]{1,12}(?:\.\d+)?)\s*(万元|元)'
)


def _money_hit(regex, text):
    """按 ``数字 + 元/万元`` 取金额，保持原文单位，不做换算。"""
    match = regex.search(text or '')
    if not match:
        return ''
    return match.group(1).replace(',', '').replace('，', '') + match.group(2)


#: 章节式标签（原文**不带冒号**，``LABEL_RE`` 匹配不到）。实测：
#: ``六、其他补充事宜 无`` ／ ``其他补充事宜 1、备案编号：…``
_SECTION_TAIL_RE = re.compile(r'(?:其他补充事宜|其它补充事宜|其他事项|其他说明|其他要求说明)\s*[:：]?\s*')
#: 章节末尾的下一节标题。**只认中文数字序号**（``七、`` ``八、``），
#: 不认阿拉伯数字序号 —— 后者正是「其他补充事宜」正文自己的列表编号
#: （``1、备案编号：… 2、采购品目：…``），截在它会丢掉正文。
_TAIL_HEADING_RE = re.compile(r'[\s。；;、]?([一二三四五六七八九十]{1,3}[、.．](?=[\u4e00-\u9fff]))')


def _section_tail(text, cuts):
    """取「其他补充事宜」这类无冒号章节标签后面的内容（到下一个边界为止）。"""
    match = _SECTION_TAIL_RE.search(text or '')
    if not match:
        return ''
    raw = text or ''
    end = _value_end(raw, match.end(), cuts)
    # 正文拼接来源（JSON 记录渲染）可能把段落折平，章节标题不在行首也要截断，
    # 否则会把「七、对本次招标提出询问」整段吞进来。值本身以序号开头时不动它。
    heading = _TAIL_HEADING_RE.search(raw, match.end())
    if heading and match.end() < heading.start(1) < end:
        end = heading.start(1)
    return _clean_value(raw[match.end():end])


#: 甲方类型的**推断**规则。顺序即优先级：基层组织 > 政府机关 > 事业单位 >
#: 国有企业 > 民营企业（兜底：名称带「公司」但没命中任何特征词）。
#: 这是模块里**唯一**不是"原文搬运"的字段，用户明确要求"初步判断的能填就填"，
#: 因此落库时 evidence.label 固定写「按甲方名称判断」。
_CLIENT_TYPE_RULES = (
    ('基层组织', r'村民委员会|居民委员会|社区|村委会|居委会'),
    ('政府机关', r'人民政府|管理委员会|管委会|街道办事处|街道办|人民防空|'
                 r'财政局|税务局|公安局|人民检察院|人民法院|管理局|监督局|'
                 r'委员会|办公室|公署|指挥部'),
    ('事业单位', r'医院|卫生院|疾病预防|学校|大学|学院|中学|小学|幼儿园|'
                 r'研究院|研究所|设计院|中心|馆|站|公园|疗养院|福利院|'
                 r'档案馆|图书馆|文化馆|体育馆|电视台|报社'),
    ('国有企业', r'集团有限公司|集团公司|控股|国有资产|国资|城投|交投|水投|建投|'
                 r'产投|投资集团|发展集团|电力|电网|烟草|铁路|航空|航天|石油|石化|'
                 r'燃气|水务|港口|机场|地铁|轨道|银行|保险|中国电信|中国移动|中国联通'),
)


def _client_type(name):
    """按甲方名称初步判断单位性质；判断不出返回空（不硬猜）。"""
    value = re.sub(r'\s+', '', name or '')
    if not value:
        return ''
    for kind, pattern in _CLIENT_TYPE_RULES:
        if re.search(pattern, value):
            return kind
    if re.search(r'有限责任公司|股份有限公司|有限公司|公司|集团|厂|商行|合作社', value):
        return '民营企业'
    return ''


#: 编号类字段的清洗。实测这批公告把编号**写在一句话里**：
#: ``项目编号：0730-2613010605/01）招标人为 航发优材（镇江）钛合金精密成型有限公司 ，
#: 招标项目资金已落实。本项目已具备招标条件，现对…`` —— 右边界找不到（句子里没有
#: 下一个标签），整句 90~97 字全进了「项目编号」，8 条候选都是这样。
#: 这类模板的编号后面紧跟一个右括号，右括号之后就是套话，据此截断。
_CODE_NOISE_RE = re.compile(
    r'(?:招标人为|采购人为|已由|现对|进行公开招标|本项目已具备|按规定|项目资金来源|'
    r'资金来源|采购进行|项目类别|招标人|采购人|项目名称|项目概况)'
)
#: 编号的正常形态：字母/数字/连字符/斜杠/点/括号（可含少量中文，如 ``GT（采）20260744``），
#: 4~40 字，且至少含一个字母或数字。带空格的（``2026-10-09 17:20`` 被误当编号）
#: 与含「编号」二字的（``标包编号/3.`` 是标签自己）一律判无效。
_CODE_OK_RE = re.compile(
    r'^(?=.*[0-9A-Za-z])[0-9A-Za-z\u4e00-\u9fff\-_/\.．（）()【】\[\]]{4,40}$'
)


def _code_value(value):
    """编号清洗：截掉句子里的套话，再按形态判有效；不像编号的一律判空。"""
    text = re.sub(r'\s+', ' ', value or '').strip()
    if not text:
        return ''
    # 句号前就是完整编号时直接取之（实测 ``CZ2025005716。1.3 项目类别：…``）。
    head = text.split('。', 1)[0].strip()
    if _CODE_OK_RE.match(head):
        text = head
    else:
        noise = _CODE_NOISE_RE.search(text)
        if noise is not None and noise.start() > 0:
            text = text[:noise.start()]
        text = text.strip(' （）()【】[]。.、,，；;')
    # 右边界落在「2.项目名称：」中间时编号会拖一个隔符（实测 ``SC0116-2026-0012.``）。
    text = text.rstrip(' .．、,，;；:：-_')
    # 编号在原文里被 <span> 拆成多行（``（项目编号：\\n0730-2613010640\\n/01\\n）``），
    # 提纯阶段把换行折成空格 → 中间的空格让形态校验把编号判空（实测丢 2 条真实编号）。
    # 编号本身不含空白，去掉再判。带空格的日期时间戳（``2026-10-09 17:20``）去掉空格后
    # 含 ``:``，仍会被 ``_CODE_OK_RE`` 挡下，不会因此把发布时间当成编号。
    text = re.sub(r'[\s\u3000]+', '', text)
    if '编号' in text or not _CODE_OK_RE.match(text):
        return ''
    return text


def extract_details(title, text, mapped=None, published='', url='', source_name=''):
    mapped = mapped or {}
    # 存量数据（JSON 分支渠道）的正文是接口记录 dump，先渲染成「字段：值」再抽字段，
    # 否则正则在 JSON 串上乱抓 —— 实测 项目编号 抓到 1284 字的转义 HTML。
    text = _unwrap_json_tail(text or '')
    labels = {}
    cuts = _boundaries(text)
    for match in LABEL_RE.finditer(text):
        key = match.group().rstrip('：:').strip()
        if key not in LABELS:
            continue
        value = _clean_value(text[match.end():_value_end(text, match.end(), cuts)])
        if value and not re.search(r'\*{2}|点击登录|登录后|登录查看', value):
            labels.setdefault(key, value)
    fields = {key: '' for key in BASE_FIELDS}
    evidence = {}

    def take(target, source_keys, mapped_keys=(), clean=None):
        """按「接口映射字段 → 正文标签」优先级取第一个非空值。

        ``clean`` 会在判定非空**之前**执行：清洗后判为垃圾（返回空）就继续试下一个
        同义标签，避免把「地址:地址:」这类模板占位当成值落库。
        """
        for key in mapped_keys:
            value = str(mapped.get(key) or '').strip()
            if clean:
                value = clean(value)
            if value:
                fields[target] = value; evidence[target] = {'label': key, 'text': value}; return
        for key in source_keys:
            value = labels.get(key) or ''
            if clean:
                value = clean(value)
            if value:
                fields[target] = value; evidence[target] = {'label': key, 'text': value}; return

    fields['项目名称'] = title
    fields['招标公告网址'] = url
    fields['招标时间'] = date_value(published)
    take('甲方名称', ('甲方名称', '采购人名称', '采购单位', '采购人', '招标人', '招标单位', '业主'),
         ('purchaser', 'buyer', 'client_name', 'client'), clean=_org_value)
    take('代理机构', ('招标代理机构', '采购代理机构', '代理机构', '代理单位', '招标代理'),
         ('agency', 'agent'), clean=_org_value)
    take('项目区域', ('项目区域',), ('region',))
    # 地点类标签优先级：项目专用 > 履约 > 服务 > 所在地区。逐个试，取第一个**像地点**的：
    # 实测「中航招标网」的 `服务地点：根据甲方要求`（不是地点）后跟 `项目所在地区：江苏省镇江市`
    # （是地点），只看第一个命中会把真实地点丢掉。
    location = ''
    for key in _LOCATION_KEYS:
        candidate = labels.get(key)
        if not candidate:
            continue
        candidate = re.sub(r'\s*[,，、]\s*', '', candidate)[:120].rstrip()
        if _plausible_location(candidate):
            location = candidate
            break
    if location:
        fields['项目实施地点'] = location
        if not fields['项目区域']:
            county = re.search(r'(?:自治州|自治区|州|市|省)([^省州市\s,，、]{1,8}(?:县|区|市))', location)
            region = (_plausible_region(county[1] if county else location)
                      or _region_from_org(location))
            if region:
                fields['项目区域'] = region
                evidence['项目区域'] = {'label': '项目实施地点', 'text': f'{location} → {region}'}
    # 项目区域是全字段里最空的（实测 34/52），而甲方名称是强信号：公告里的采购人
    # 本来就写成「省+市+区/县+…」，拆出行政区划链就是区域。属于「初步判断」，
    # evidence.label 固定写「按甲方名称判断」，和原文搬运的字段区分得开。
    # 这条只在前面全都取不到时才用，不覆盖原文/地点推出来的结果。
    if not fields['项目区域'] and fields['甲方名称']:
        region = _region_from_org(fields['甲方名称'])
        if region:
            fields['项目区域'] = region
            evidence['项目区域'] = {'label': '按甲方名称判断',
                                    'text': f'{fields["甲方名称"]} → {region}'}
    # 兜底闸：``甲方：`` 后面直接跟公司名时，兜底逻辑可能把机构名当成了区域
    # （实测 ``梅河口市阜康酒精有限责任公司`` 落进 项目区域 / 项目实施地点 各 2 条）。
    if fields['项目区域'] and not _plausible_region(fields['项目区域']):
        fields['项目区域'] = ''
        evidence.pop('项目区域', None)
    take('项目类型', ('项目类型', '服务类型', '采购类别'), ('project_type', 'projectType'))
    if fields['项目类型'] in ('', '服务'):
        for terms, kind in ((('劳务派遣',), '劳务派遣'), (('劳务外包',), '劳务外包'),
                            (('安保', '保安'), '安保服务'), (('保洁', '清扫', '环卫'), '保洁服务'),
                            (('物业',), '物业服务'), (('食堂', '餐饮', '配餐'), '餐饮服务'),
                            (('装卸', '搬运', '分拣'), '装卸搬运'), (('绿化', '管护', '养护'), '绿化养护'),
                            (('司机', '驾驶', '运输', '物流'), '运输服务'), (('后勤',), '后勤服务'),
                            (('人力资源',), '人力资源服务')):
            if any(term in title for term in terms):
                fields['项目类型'] = kind; evidence['项目类型'] = {'label': '公告标题', 'text': title}; break
    fields['招标金额'], amount_evidence = public_amount(text, mapped)
    if amount_evidence:
        evidence['招标金额'] = {'label': '公开金额', 'text': fields['招标金额'], 'items': amount_evidence}
    take('报名截止时间', ('报名截止时间', '报名截止', '文件获取截止时间', '购标截止时间',
                          '获取采购文件截止时间', '报名时间', '响应截止时间'), ('registration_deadline',))
    if not fields['报名截止时间']:
        fallback = _doc_deadline(text)
        if fallback:
            fields['报名截止时间'] = fallback
            evidence['报名截止时间'] = {'label': '文件获取截止', 'text': fallback}
    fields['报名截止时间'] = date_value(fields['报名截止时间'])
    take('开标时间', ('截标/开标时间', '响应文件递交截止时间和开标时间', '开标时间', '投标截止时间',
                      '响应文件提交截止时间', '递交截止时间', '响应文件递交截止时间'), ('opening_time',))
    fields['开标时间'] = date_value(fields['开标时间'])
    take('服务期限', ('服务期说明', '合同履行期限', '履约期限', '项目服务期', '项目期限',
                      '服务期限', '服务期'), ('service_period',))
    if not fields['服务期限']:
        days = labels.get('服务期（天）') or labels.get('服务期(天)')
        if days:
            fields['服务期限'] = days + '天'
    take('报名费用', ('报名费用', '文件发售金额(元)', '文件发售金额（元）', '采购文件售价', '文件售价',
                      '招标文件费用', '获取招标文件费用', '招标文件售价', '采购文件费用',
                      '电子标书费售价', '标书费售价', '标书费', '文件费', '工本费', '文件资料费',
                      '平台服务费', '平台使用费'),
         ('registration_fee',))
    if fields['报名费用'] and re.fullmatch(r'\d+(?:\.\d+)?', fields['报名费用']):
        fields['报名费用'] += '元'
    if not fields['报名费用']:
        fee = _file_fee(text)
        if fee:
            fields['报名费用'] = fee
            evidence['报名费用'] = {'label': '原文售价表述', 'text': fee}
    take('项目情况简述', ('项目概况', '项目情况简述', '项目简介', '采购项目简介', '项目内容',
                          '服务内容', '采购内容', '采购范围', '招标范围', '服务范围', '规模'),
         ('summary',))
    for target, sources in (('资质要求', ('供应商资质要求', '供应商资格要求', '申请人的资格要求',
                                          '资格要求', '合格供应商应具备的资格条件',
                                          '供应商应具备的资格条件', '投标人资格要求',
                                          '申请人资格要求', '特定资格要求', '资质要求')),
                            ('基本要求', ('供应商基本要求',)),
                            ('业绩要求', ('供应商业绩要求', '业绩要求', '投标人业绩要求',
                                          '类似业绩要求', '类似业绩', '类似项目业绩', '供应商业绩')),
                            ('其他要求', ('供应商其他要求', '其他要求', '其他补充事宜', '其它补充事宜',
                                          '其他事项', '其他说明', '其他要求说明')),
                            ('联合体投标', ('是否接受联合体投标', '是否接受联合体参与', '是否接受联合体',
                                            '是否允许联合体投标', '是否允许联合体', '联合体投标')),
                            ('采购方式', ('采购方式', '招标方式')),
                            ('项目编号', ('项目编号', '采购项目编号', '招标编号', '采购编号',
                                          '招标项目编号', '标段/包编号')),
                            ('所属行业', ('所属行业', '所属行业分类', '行业分类'))):
        take(target, sources)

    # ---- 下面是标签白名单之外、但原文确实写了的值（写法太散，用句式兜） ----
    joint = _joint_venture(text) or _joint_venture(fields['联合体投标'])
    if joint:
        raw = fields['联合体投标']
        if raw and raw != joint:
            evidence['联合体投标'] = {'label': '原文「联合体」表述', 'text': raw}
        elif not raw:
            evidence['联合体投标'] = {'label': '原文「联合体」表述', 'text': joint}
        fields['联合体投标'] = joint
    if not fields['其他要求']:
        tail = _section_tail(text, cuts)
        if tail:
            fields['其他要求'] = tail
            evidence['其他要求'] = {'label': '其他补充事宜', 'text': tail}
    # 采购方式：这套政府采购模板把方式织进首句（``采用竞争性磋商方式进行采购``），
    # 没有 ``采购方式：`` 标签，白名单抓不到。
    if not fields['采购方式']:
        method = _method_value(text)
        if method:
            fields['采购方式'] = method
            evidence['采购方式'] = {'label': '原文「采用…方式」表述', 'text': method}
    # 项目情况简述：``三、采购项目简介`` 这类章节标题不带冒号，白名单抓不到。
    if not fields['项目情况简述']:
        body = _section_body(text, cuts)
        if body:
            fields['项目情况简述'] = body
            evidence['项目情况简述'] = {'label': '采购项目简介', 'text': body}
    for key, regex in (('投标保证金（元）', _DEPOSIT_RE), ('中标服务费（元）', _AGENT_FEE_RE)):
        if not fields[key]:
            amount = _money_hit(regex, text)
            if amount:
                fields[key] = amount
                evidence[key] = {'label': '原文金额表述', 'text': amount}
    # 甲方类型是**推断值**（模块里唯一一个）：evidence 写明来源，便于人工复核。
    if fields['甲方名称']:
        kind = _client_type(fields['甲方名称'])
        if kind:
            fields['甲方类型'] = kind
            evidence['甲方类型'] = {'label': '按甲方名称判断', 'text': f'{fields["甲方名称"]} → {kind}'}
    # 编号类字段形态校验：整句粘贴 / 表头串 / 发布时间都挡在外面（判空好过落垃圾）。
    for key in ('项目编号', '采购项目编号', '招标编号', '采购编号', '标段/包编号', '招标项目编号'):
        raw = fields.get(key)
        if not raw:
            continue
        cleaned = _code_value(raw)
        if cleaned:
            fields[key] = cleaned
        else:
            fields.pop(key, None)
            evidence.pop(key, None)
    # 短值字段的安全网：中缝标题规则修好后本不该超限，留着兜右边界再次失守的情况。
    # 取「第一句」而不是「最后一截」—— 这类字段的超长值里，第一句才是字段本身。
    for key, cap in _FIELD_MAX.items():
        value = fields.get(key)
        if value and len(value) > cap:
            trimmed = _truncate_at_sentence(value, cap, first_sentence=True).strip()
            if trimmed:
                fields[key] = trimmed
                if key in evidence:
                    evidence[key] = {**evidence[key], 'text': trimmed}
    # 采购方式只认真正的方式名：实测右边界失守时落进来的是
    # ``1 SECTION_CODE：I5101005273008304001001`` 这类接口字段残渣，
    # 判空比留一条看着像值的东西更好（缺失时人工一眼就知道要补）。
    method = fields.get('采购方式')
    if method and not _METHOD_OK_RE.search(method):
        fields['采购方式'] = ''
        evidence.pop('采购方式', None)

    # 频道首页链接对用户没意义：正文里给了真实原文链接就换掉。
    if _is_channel_home(url):
        real_link = _plausible_link(labels.get('原文链接') or labels.get('公告链接') or '')
        if real_link:
            fields['招标公告网址'] = real_link
            evidence['招标公告网址'] = {'label': '原文链接', 'text': real_link}
    highlights = []
    summary = fields['项目情况简述']
    for pattern in (r'\d+名(?:保安|安保|保洁|人员|员工)(?:人员)?', r'(?:累计|共计)\d+个月', r'\d+班倒[^，。；）)]{0,20}\d+小时'):
        match = re.search(pattern, summary)
        if match:
            highlights.append(match.group())
    if fields.get('资质要求'):
        highlights.append(fields['资质要求'])
    meaningful = sum(bool(fields.get(key)) for key in
                     ('甲方名称', '代理机构', '项目编号', '项目区域', '项目情况简述', '报名截止时间'))
    status = '需人工查看' if blocked_text(text) else '已读取' if meaningful >= 3 else '部分读取'
    undisclosed = re.search(r'(?:最高限价|预算|采购金额|招标金额).{0,18}(?:不予公开|不公开|不公布|保密|暂不披露)', text or '')
    amount_status = '已提取' if fields['招标金额'] else '未公开' if undisclosed else '原文未找到' if meaningful >= 3 and not blocked_text(text) else '待读取'
    return {'version': VERSION, 'fields': fields, 'evidence': evidence, 'highlights': highlights,
            'sourceUrl': url, 'sourceName': source_name, 'status': status,
            'amountStatus': amount_status,
            'amountMessage': undisclosed.group() if undisclosed else '',
            # message 只留给「正文被验证墙挡住」这一类**人确实需要知道**的情况。
            # 旧版还会下发「仅读取部分信息，缺失字段留空」——用户在页面上看到的就是
            # 这句废话小字（2026-10-10 明确要求去掉）。缺失字段在界面上本来就显示为
            # 空值，不需要再解释一遍；真要说明的只有「抓不到正文」。
            'message': '正文需要网站验证，暂未读取' if blocked_text(text) else '',
            'readAt': datetime.now(TZ).isoformat()}


def _filled_count(fields):
    """非空字段数。用于在同一 URL 的两份正文（缓存 / 联网）里挑信息更全的那份。"""
    return sum(1 for value in (fields or {}).values() if str(value or '').strip())


def promotion_fields(detail):
    fields = dict((detail or {}).get('fields') or {})
    summary = [fields.get('项目情况简述', '')]
    for key in ('项目编号', '采购方式', '所属行业', '甲方类型', '项目实施地点', '资质要求', '业绩要求',
                '其他要求', '联合体投标', '投标保证金（元）', '中标服务费（元）'):
        if fields.get(key):
            summary.append(f'{key}：{fields[key]}')
    fields['项目情况简述'] = '\n'.join(value for value in summary if value)
    return fields


def sdic_entries(html, base_url):
    """国投列表使用已知 onclick 链接；不执行页面脚本。"""
    soup = BeautifulSoup(html, 'html.parser')
    entries, seen = [], set()
    for row in soup.select('tr[onclick]'):
        match = re.fullmatch(r"urlChange\('([\w-]+)'\s*,\s*'([\w-]+)'\)", row.get('onclick', '').strip())
        cells = row.select('td')
        if not match or len(cells) < 3:
            continue
        title = re.sub(r'^\[[^\]]+\]\s*', '', cells[0].get_text(' ', strip=True))
        url = urljoin(base_url, f'/cgxx/ggDetail?gcGuid={match[2]}&ggGuid={match[1]}')
        if url in seen:
            continue
        seen.add(url)
        entries.append({'title': title, 'url': url, 'published': date_value(cells[-1].get_text(' ', strip=True)),
                        'deadline': date_value(cells[1].get_text(' ', strip=True))})
    return entries


class TenderDetailsService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def retry_due(detail, now):
        """公开金额缺失继续补取；已验证未找到/未公开的正文隔六小时复查。"""
        if detail.get('version') != VERSION:
            return True
        if detail.get('status') == '已读取' and (detail.get('fields') or {}).get('招标金额'):
            return False
        raw = detail.get('lastAttempt')
        if not raw:
            return True
        try:
            attempted = datetime.fromisoformat(raw)
            if attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=TZ)
        except ValueError:
            return True
        delay = timedelta(hours=6) if detail.get('amountStatus') in ('未公开', '原文未找到') and detail.get('status') == '已读取' else timedelta(minutes=30)
        return now - attempted >= delay

    def refresh_missing(self, catalog=(), limit=8, offline=False, force=False):
        """补取「公告候选」的原公告字段。

        ``offline=True`` 时不联网，只用已缓存的正文重新解析 —— 用于**解析器升版后
        确定性地重算存量**：联网再抓一次会换回一份排版不同的页面（实测四川站同一 URL
        两次抓取，正文换行结构不同），字段结果因此抖动，没法判断改动到底有没有效。
        **注意**：缓存正文可能比联网正文少内容，离线回写会丢掉"只有联网正文里才有"的
        字段（实测 `报名截止时间`）。离线模式只用于对照，**正式回写请走联网**。

        ``force=True`` 时跳过 30 分钟 / 6 小时的重试窗口，立刻重算（仍受"落地 24 小时内"
        的业务闸约束）。同版本下 :meth:`_store` 会用旧的非空值兜住新算出来的空值，
        因此强制重算不会造成数据丢失，只会补齐。
        """
        now = datetime.now(TZ)
        with self.sessions() as session:
            projects = session.scalars(select(Project).where(Project.workflow_stage == '公告候选')).all()
            pending = []
            for p in projects:
                landing = (p.metadata_ or {}).get('landing') or {}
                detail = (p.metadata_ or {}).get('tender_detail') or {}
                created = datetime.fromisoformat(landing.get('created_at') or p.created_at.isoformat())
                if created.tzinfo is None:
                    created = created.replace(tzinfo=TZ)
                if now - created >= timedelta(hours=24):
                    continue
                if not force and not self.retry_due(detail, now):
                    continue
                pointers = session.scalars(select(SourcePointer).where(SourcePointer.project_id == p.id)).all()
                source_url = detail.get('sourceUrl') or landing.get('source_url') or ''
                source_name = detail.get('sourceName') or landing.get('channel_name') or ''
                cached = next((sp.original_text for sp in pointers if sp.source_url == source_url and sp.original_text), '')
                pending.append((p.id, p.name, {**landing, 'source_url': source_url, 'channel_name': source_name,
                                            'last_attempt': detail.get('lastAttempt') or ''}, cached))
            pending.sort(key=lambda item: item[2]['last_attempt'])
            pending = pending[:limit]
        reports, sdic_cache = [], {}
        # 模型整理预算：默认关闭时该账本不会被使用；开启后单轮最多整理 settings.ai_max_per_run 条。
        ai_ledger = {"used": 0, "limit": settings.ai_max_per_run}
        with httpx.Client(timeout=8, follow_redirects=True, trust_env=False, headers={'User-Agent': 'BidOS/0.3 public-notice-collector'}) as client:
            for pid, title, landing, cached in pending:
                source_url, source_name = landing.get('source_url') or '', landing.get('channel_name') or ''
                published = landing.get('published_at') or ''
                body = cached or ''
                fetched_body = ''
                if not offline:
                    try:
                        response = client.get(source_url); response.raise_for_status()
                        fetched = notice_text(response.text)
                        if notice_matches(title, fetched) or blocked_text(fetched):
                            body = fetched_body = fetched
                    except httpx.HTTPError:
                        pass
                detail = extract_details(title, body, published=published, url=source_url, source_name=source_name)
                # 目录中的国投源按完整项目名/轮次匹配，禁止把首次公告套给二次公告。
                if detail['status'] != '已读取' and not offline:
                    for channel in catalog:
                        if (urlparse(channel['url']).hostname or '') not in ('www.sdicc.com.cn', 'sdicc.com.cn'):
                            continue
                        try:
                            if channel['url'] not in sdic_cache:
                                response = client.get(channel['url']); response.raise_for_status()
                                sdic_cache[channel['url']] = sdic_entries(response.text, channel['url'])
                            expected_day = date_value(landing.get('published_at') or '')[:10]
                            item = next((item for item in sdic_cache[channel['url']] if notice_key(item['title']) == notice_key(title)
                                and (not expected_day or item['published'][:10] == expected_day)), None)
                            if not item:
                                continue
                            response = client.get(item['url']); response.raise_for_status()
                            alternate_body = notice_text(response.text)
                            if not notice_matches(title, alternate_body, head=1000):
                                continue
                            alternate = extract_details(title, alternate_body, published=landing.get('published_at') or item['published'], url=item['url'], source_name=channel['name'])
                            if alternate['status'] == '已读取':
                                detail, body = alternate, alternate_body
                                break
                        except httpx.HTTPError:
                            continue
                # 同一 URL 两次抓取的**排版可能不同**（四川站实测），而排版差异会让
                # 某些句式失效：联网那份把「获取招标文件 / 方式：在线获取 / 售价：0元」
                # 保留成三行，缓存那份折成了一行 —— 同一份正则只认后者。
                # 两份正文都在手上，就取**抽出字段更多**的那份，而不是默认信联网那份；
                # 否则一次排版抖动就能把已读到的事实清掉（本轮实测 报名费用 掉 2 条）。
                # 选了什么记在 bodyPick 里，便于回查。
                if fetched_body and cached and fetched_body != cached:
                    alt = extract_details(title, cached, published=published,
                                          url=source_url, source_name=source_name)
                    if _filled_count(alt['fields']) > _filled_count(detail['fields']):
                        detail, body = alt, cached
                        detail['bodyPick'] = 'cache'
                    else:
                        detail['bodyPick'] = 'fetch'
                if not offline:
                    detail = self._ai_enrich(title, body, detail, ai_ledger)
                self._store(pid, detail, body)
                reports.append({'id': pid, 'status': detail['status']})
        return reports

    @staticmethod
    def _ai_enrich(title, body, detail, ledger):
        """模型整理钩子（2026-10-09）。

        **默认关闭**：未启用或未配置时直接原样返回，接管前后的采集行为与结果完全一致。
        启用后只补规则留空的文字型字段，并在 detail['aiAssist'] 留下本次调用记录；
        模型失败不影响规则结果，也不阻断采集。
        """
        from app.ai.registry import get_provider

        provider = get_provider()
        if provider is None:
            return detail
        if ledger.get('used', 0) >= ledger.get('limit', 0):
            detail = dict(detail)
            detail['aiAssist'] = {
                'status': 'skipped',
                'reason': f"本轮模型整理预算已用尽（上限 {ledger.get('limit', 0)} 条）",
                'provider': provider.name,
                'model': provider.model,
            }
            return detail
        ledger['used'] = ledger.get('used', 0) + 1
        from app.services.ai_enrichment import enrich_detail

        try:
            return enrich_detail(provider, title=title, body=body, detail=detail)
        except Exception as exc:  # noqa: BLE001 - 整理失败绝不允许中断采集
            detail = dict(detail)
            detail['aiAssist'] = {
                'status': 'failed',
                'provider': provider.name,
                'model': provider.model,
                'error': {'kind': 'unexpected', 'message': f'{type(exc).__name__}: {exc}'},
                'note': '模型整理出现未预期异常，采集继续，字段保持规则结果。',
            }
            return detail

    @staticmethod
    def _mirror(new, old):
        """镜像列（``client_name`` / ``region`` / ``project_type``）的更新口径。

        新值非空 → 直接覆盖。新值为空 → **不能无条件保留旧值**：旧值可能就是站点残留
        （实测 ``client_name=正式会员“登录”后可浏览】``），保留会让页面在
        ``tender_detail.fields`` 已判定为空之后**继续显示它**（用户看到的就是「内容不对」）。
        只有「不是因为垃圾才被清空」的旧值才保留。
        """
        if new:
            return new
        return '' if _looks_like_residue(str(old or '')) else old

    def _store(self, pid, detail, body):
        with self.sessions() as session:
            p = session.get(Project, pid, with_for_update=True)
            if p is None or p.workflow_stage != '公告候选' or (p.metadata_ or {}).get('candidate_promotion'):
                return
            old = (p.metadata_ or {}).get('tender_detail') or {}
            # 「已读取」不等于「读得对」：实测有记录的 `甲方名称/代理机构/项目编号` 三条全是
            # 「正式会员“登录”后可浏览」，状态却是「已读取」。若还按老口径保护，这条记录会被
            # 永久冻在垃圾值上（`preserve_body` 每轮都命中，新解析器永远写不进去）。
            old_residue = any(_looks_like_residue(str(v or ''))
                              for v in (old.get('fields') or {}).values())
            # 还要「真的变差」才保护：只看状态会把「旧值里塞着垃圾、新值字段更多」的情形
            # 也冻住（实测 `四川省盐业集团…` 两条：旧值 项目编号=「标包编号/3.」、
            # 甲方名称带「采购」尾巴、甲方类型缺失，却被「已读取 → 部分读取」一直保护）。
            old_filled = sum(1 for v in (old.get('fields') or {}).values() if str(v or '').strip())
            new_filled = sum(1 for v in detail['fields'].values() if str(v or '').strip())
            preserve_body = (old.get('status') == '已读取' and detail['status'] != '已读取'
                             and not old_residue and new_filled < old_filled)
            if preserve_body:
                # 临时访问失败不丢已读取事实，同时记录尝试，避免每轮空跑。
                detail = {**old, 'lastAttempt': datetime.now(TZ).isoformat(), 'lastAttemptStatus': detail['status']}
            else:
                # 继承旧值 + 保留模型整理痕迹。两条规则分开：
                # ① **模型补的值无条件带走**（evidence.ai=True）。它不是规则输出，
                #    不会因为规则升版而失效 —— 实测回填时漏掉这一步，把
                #    `甲方名称=山东电力中心医院`、`项目实施地点=…东荆河路318号` 这类
                #    模型补的事实全清空了。
                # ② **规则产出的旧值只在同版本时继承**：升版意味着旧规则正是要修的对象，
                #    实测旧规则把 `项目区域` 写成 `/`、把「根据甲方要求。」写成地点，
                #    无条件继承会让这些垃圾因为"新值为空"而存活下来；同版本下仍照旧继承，
                #    避免一轮抓取失败把已读到的事实清空。
                evidence_old = old.get('evidence') or {}
                same_version = old.get('version') == VERSION
                inherited = {}
                for key, value in (old.get('fields') or {}).items():
                    if not value or detail['fields'].get(key):
                        continue
                    ai_filled = isinstance(evidence_old.get(key), dict) and evidence_old[key].get('ai') is True
                    if ai_filled or (same_version and _inherit_ok(key, value)):
                        inherited[key] = value
                detail['fields'] = {**detail['fields'], **inherited}
                # 保留模型整理痕迹：AI 补过的字段必须连同 evidence / aiAssist 一起带走，
                # 否则规则刷新一次，页面就看不出哪些字段是模型补的，回滚也失去依据。
                carried = {
                    key: value
                    for key, value in (old.get('evidence') or {}).items()
                    if isinstance(value, dict) and value.get('ai') is True and key in detail['fields']
                }
                if carried:
                    detail['evidence'] = {**(detail.get('evidence') or {}), **carried}
                    if old.get('aiAssist'):
                        detail['aiAssist'] = old['aiAssist']
            fields = detail['fields']
            if fields.get('招标金额'):
                detail['amountStatus'] = '已提取'
            detail['lastAttempt'] = datetime.now(TZ).isoformat()
            p.metadata_ = {**(p.metadata_ or {}), 'tender_detail': detail}
            p.client_name = self._mirror(fields['甲方名称'], p.client_name)
            p.region = self._mirror(fields['项目区域'], p.region)
            p.project_type = self._mirror(fields['项目类型'], p.project_type)
            if fields['报名截止时间']:
                p.deadline = datetime.fromisoformat(fields['报名截止时间']).replace(tzinfo=TZ)
            url = detail['sourceUrl']
            pointer_id = 'bidos_ptr_detail_' + hashlib.sha1(f'{pid}|{url}'.encode()).hexdigest()[:24]
            pointer = session.get(SourcePointer, pointer_id)
            if pointer is None:
                pointer = SourcePointer(id=pointer_id, project_id=pid, source_name=detail['sourceName'] or '公告详情',
                    source_type='web', source_url=url, trust_level='official' if (urlparse(url).hostname or '') in ('www.sdicc.com.cn', 'sdicc.com.cn') else 'public')
                session.add(pointer)
            if not preserve_body or not pointer.original_text:
                pointer.original_text = body
            pointer.metadata_ = {'purpose': 'tender_detail', 'read_at': detail['readAt'], 'fields': fields, 'evidence': detail['evidence']}
            session.commit()
