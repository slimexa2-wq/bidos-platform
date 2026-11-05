"""ExcelConnector / 数据字典 / 入库脚本 的端到端测试。

测试用临时 xlsx 在 ``tempfile.mkdtemp()`` 下生成，测试结束清理，不污染仓库。
所有断言都对应交接文档的硬约束：
- §4  SourcePointer 字段必须填满
- §10 Phase 0 数据字典 8 项
- §12.6 / FAILURE_MODES §1 失败必须可见，不静默返回空
- §14 禁止写回原表
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from openpyxl import Workbook  # noqa: E402

from app.connectors.base import ConnectorContext  # noqa: E402
from app.connectors.excel_connector import (  # noqa: E402
    ExcelConnector,
    excel_serial_to_datetime,
    find_lock_file,
    is_null_token,
    normalize_cell,
    parse_date_text,
    parse_money_text,
    read_merged_ranges,
    trim_trailing_empty_columns,
)
from app.domain import SourceType, TrustLevel  # noqa: E402
from app.services.source_pointer import SourcePointerService  # noqa: E402


def load_script(name: str):
    """按文件路径加载 scripts/*.py，避免与站点包重名。"""
    path = ROOT / "scripts" / f"{name}.py"
    module_name = f"bidos_script_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 需要 cls.__module__ 能在 sys.modules 里找到
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


import_excel = load_script("import_excel")
build_data_dictionary = load_script("build_data_dictionary")

MAPPING_PATH = ROOT / "apps" / "api" / "app" / "config" / "field_mapping.yaml"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExcelConnectorTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="bidos_excel_test_"))
        cls.mapping = import_excel.load_mapping(MAPPING_PATH)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        # 每个测试独立子目录：避免用例之间互相污染输入文件
        self.case_dir = Path(tempfile.mkdtemp(dir=self.tmp))

    # field_mapping.yaml 的必需 Sheet 列表随真表更新而变（bid_plan 已到 41 个）。
    # 夹具按配置**动态补齐**缺失 Sheet（空结构占位），保证「Sheet 缺失即失败」
    # 的显式校验不被测试夹具漂移误伤；映射再更新也不需要回来改这里。
    def ensure_required_sheets(self, wb, source_id: str, keep: set[str]) -> None:
        source = next(
            (s for s in self.mapping.sources if s.source_id == source_id), None
        )
        if source is None:
            return
        existing = {ws.title for ws in wb.worksheets}
        for name in source.sheets or []:
            if name in existing:
                continue
            ws = wb.create_sheet(title=name)
            if name in keep:
                # 语义重要的 Sheet 复制一份真实表头，保证字段抽取可跑
                header = keep[name]
                ws.append([f"{name}（占位标题行）"])
                ws.append(list(header))
            else:
                ws.append([f"{name}（占位标题行）"])
                ws.append([f"{name}_占位列"])

    # ── 样例构造 ────────────────────────────────────────────
    # ⚠️ 样例一律照抄**真实业务表的列名 / Sheet 名 / 表头行**，
    #    否则测试通过但真表跑不通（这正是本次修过的坑）。
    def make_blind_bid(self, name: str = "盲投项目信息 收集汇总表2026(3).xlsx") -> Path:
        path = self.case_dir / name
        wb = Workbook()
        ws = wb.active
        ws.title = "1月"
        ws.append(["盲投项目信息收集汇总表0202"])  # 真实表：第 1 行是标题，第 2 行才是表头
        ws.append([
            "序号", "项目区域", "项目类型", "招标时间", "报名截止时间", "开标时间", "甲方名称",
            "代理机构", "项目名称", "招标金额", "服务期限", "项目情况简述", "招标公告网址",
            "报名参与情况统计", "报名费用", "备注", "中标单位", "中标金额（万元）", "服务开始日期",
        ])
        ws.append([
            1, "成都", "劳务外包", datetime(2026, 9, 1), datetime(2026, 9, 20, 18, 0),
            datetime(2026, 9, 25, 9, 30), "成都市第一人民医院", "四川某某招标代理有限公司",
            "成都市某医院后勤劳务外包服务项目", 12000000, "2年", "医院后勤",
            "https://example.com/notice/001", "报名参与", 0, None, None, None,
            datetime(2026, 11, 1),
        ])
        ws.append([])  # 空行必须被跳过
        ws.append([
            3, "德阳", "劳务外包", datetime(2026, 9, 8), datetime(2026, 9, 26),
            datetime(2026, 10, 8, 9, 0), "德阳市城市管理局", "/",
            "德阳市环卫保洁劳务外包项目", "880万元", "3年", "环卫保洁",
            None, "报名未参与", 0, "垫资周期过长", "未中标", None, None,
        ])
        # 内嵌重复表头行（真表把表头复制到了数据中间）→ 必须被识别并跳过
        ws.append([
            "序号", "项目区域", "项目类型", "招标时间", "报名截止时间", "开标时间", "甲方名称",
            "代理机构", "项目名称", "招标金额", "服务期限", "项目情况简述", "招标公告网址",
            "报名参与情况统计", "报名费用", "备注", "中标单位", "中标金额（万元）", "服务开始日期",
        ])
        ws.append([
            4, "宜宾", "安保服务", datetime(2026, 9, 10), datetime(2026, 9, 28),
            datetime(2026, 10, 10, 9, 0), "宜宾市某国企", "/", "宜宾市某国企安保服务项目",
            "300万元", "1年", "安保", None, "报名参与", 500, None, None, None,
        ])

        ws2 = wb.create_sheet("2026年未参与")
        ws2.append(["12月盲投未参与项目"])
        ws2.append([
            "序号", "项目类型", "开标时间", "项目名称", "甲方名称", "项目类型", "参与单位",
            "报名费（元）", "是否踏勘", "未参与原因", "中标单位", "限价", "中标价格", "折扣率",
        ])
        ws2.append([
            1, "企业", datetime(2026, 9, 9), "广元市某学校食堂劳务项目", "广元市教育局", "公开",
            "未参与", 0, "否", "利润空间不足", "四川某某劳务有限公司", 2100000, 2000000, 0.95,
        ])
        wb.create_sheet("盲投总表")  # 聚合统计表：有意不导入
        self.ensure_required_sheets(wb, "blind_bid_2026", keep={
            "2026年未参与": ["序号", "项目类型", "开标时间", "项目名称", "甲方名称", "项目类型",
                          "参与单位", "报名费（元）", "是否踏勘", "未参与原因", "中标单位",
                          "限价", "中标价格", "折扣率"],
        })
        wb.save(path)
        return path

    def make_bid_plan(self, name: str = "投标项目计划9.13.xlsx") -> Path:
        path = self.case_dir / name
        wb = Workbook()
        ws = wb.active
        ws.title = "9月第一周"
        ws.append(["9月第一周招采项目计划表"])  # 标题行
        ws.append([
            "序号", "项目类型", "项目名称", "甲方名称", "报名主体", "项目地址", "人数需求，限价",
            "计划招采时间", "招采方式", "投标保证金", "目前进度", "计划", "项目负责人",
            "踏勘协助负责人", "优先级",
        ])
        ws.append([
            1, "新增", "成都市某医院后勤劳务外包服务项目", "成都市第一人民医院", "总公司",
            "成都市", "446万元", "9月1日9时", "公开", 250000, "文件审查完成", "31日定价打印上传",
            "张伟", "李强/张伟", "完成封标",
        ])
        ws.append([
            2, "盲投", "宜宾市某国企园区安保服务项目", "宜宾临港投资集团", "保安公司", "宜宾",
            "76人,3040.303485万元/3年", "9月3日10时", "公开", "3.3万元", "已报名", "完成评估",
            "赵敏", "/", "完成评估",
        ])
        ws.append([
            3, "新增", "广元市某学校食堂劳务项目", "广元市教育局", "总公司", "广元市", "210万元",
            "9月3日13时", "询价", 0, "文件初稿制作", "2日资料上传", "孙丽", "/", "完成踏勘",
        ])
        # 旧版周计划列名（真表 12月~6月 用的就是这套）
        ws2 = wb.create_sheet("6月第四周")
        ws2.append(["6月第四周招采项目计划表"])
        ws2.append([
            "序号", "项目类型", "甲方名称", "报名主体", "项目名称", "项目地址",
            "标的（中标金额）\n人数需求", "计划招采时间", "招采方式", "投标保证金", "目前进度",
            "计划", "项目负责人", "踏勘协助负责人", "优先级", "开标人员", "配合单位",
        ])
        ws2.append([
            1, "盲投", "成都市第一人民医院", "总公司", "成都市某医院后勤劳务外包服务项目",
            "成都市", "446万元", "6月1日9时", "公开", 250000, "已开标", "跟进结果", "张伟",
            "/", "完成开标", "张伟", "总公司",
        ])
        # 月度计划/目标进度：聚合统计，有意不导入
        wb.create_sheet("9月计划、目标进度 ")
        # 合成用例：两行合并表头（第 3 列的列名只在第二行出现）
        # 合成用例：两行合并表头（第 3 列的列名只在第二行出现）
        ws3 = wb.create_sheet("两行表头")
        ws3.append(["项目名称", "招标人", None, "预算金额", "报名截止时间"])
        ws3.append([None, None, "地区", None, None])
        ws3.append(["测试项目A", "测试甲方", "成都", 100000, datetime(2026, 10, 1)])
        bid_plan_header = ["序号", "项目类型", "项目名称", "甲方名称", "报名主体", "项目地址",
                           "人数需求，限价", "计划招采时间", "招采方式", "投标保证金", "目前进度",
                           "计划", "项目负责人", "踏勘协助负责人", "优先级"]
        self.ensure_required_sheets(wb, "bid_plan_2026", keep={
            "9月第一周": bid_plan_header,
            "6月第四周": ["序号", "项目类型", "甲方名称", "报名主体", "项目名称", "项目地址",
                        "标的（中标金额）\n人数需求", "计划招采时间", "招采方式", "投标保证金",
                        "目前进度", "计划", "项目负责人", "踏勘协助负责人", "优先级", "开标人员", "配合单位"],
        })
        wb.save(path)
        return path

    def make_competitor(self, name: str = "（汇总）主要竞争对手 投标项目统计.xlsx") -> Path:
        path = self.case_dir / name
        wb = Workbook()
        ws = wb.active
        ws.title = "统计表"
        ws.append(["近3个月投标项目统计"])  # 标题行
        ws.append([
            "序号", "公司名称", "投递项目名称", "项目类型", "是否中标", "招采时间", "服务期限",
            "中标单位", "中标金额", "岗位类型", "甲方名称", "招标平台",
        ])
        # 公司名称是纵向合并（真表如此）：只有首行有值，靠合并区域向下填充
        ws.append([
            1, "四川宝航人力资源有限公司", "成都市某医院后勤劳务外包服务项目", "企业", "是",
            datetime(2026, 9, 30), "2年", "四川宝航人力资源有限公司", "88.3万元", "后勤",
            "成都市第一人民医院", "四川政府采购网",
        ])
        ws.append([
            2, None, "德阳市环卫保洁劳务外包项目", "企业", "否", datetime(2026, 9, 28), "3年",
            "德阳市某某物业有限公司", "0.0672", "保洁", "德阳市城市管理局", "四川政府采购网",
        ])
        ws.merge_cells(start_row=3, start_column=2, end_row=4, end_column=2)
        self.ensure_required_sheets(wb, "competitor_stats", keep={
            "统计表": ["序号", "公司名称", "投递项目名称", "项目类型", "是否中标", "招采时间",
                     "服务期限", "中标单位", "中标金额", "岗位类型", "甲方名称", "招标平台"],
        })
        wb.save(path)
        return path

    def make_channel(self, name: str = "盲投渠道表及重点关注企业8.14.xlsx") -> Path:
        path = self.case_dir / name
        wb = Workbook()
        ws = wb.active
        ws.title = "盲投渠道表"
        ws.append(["盲投项目渠道汇总表"])
        ws.append(["序号", "信息渠道", "网址", "筛选频率", "账号", "密码"])
        ws.append([1, "四川政府采购网", "https://www.ccgp-sichuan.gov.cn", "高（一天一次）", "scxn2024", "scxn20240306"])
        ws.append([2, "采招网", "https://www.bidcenter.com.cn/", "高（一天一次）", None, None])

        ws2 = wb.create_sheet("重点关注企业")
        ws2.append(["重点关注企业名单"])
        ws2.append(["序号", "区域", "企业名称"])
        ws2.append([1, "宜宾", "四川省宜宾普什集团有限公司"])
        ws2.append([2, None, "四川安吉物流集团有限公司"])
        ws2.merge_cells(start_row=3, start_column=2, end_row=4, end_column=2)
        wb.create_sheet("盲投关键词")  # 关键词配置：有意不导入
        self.ensure_required_sheets(wb, "blind_bid_channel", keep=set())
        wb.save(path)
        return path

    def make_total_ledger(self, name: str = "投标项目汇总9.17.xlsx") -> Path:
        """第 5 源：投标项目汇总台账（保证金 Sheet 在这里）。"""
        path = self.case_dir / name
        wb = Workbook()

        def sheet_with_header(title, header, *rows):
            ws = wb.create_sheet(title)
            ws.append([f"{title}（占位标题行）"])  # header_row: 2
            ws.append(list(header))
            for row in rows:
                ws.append(list(row))

        year_header = ["序号", "年份", "月份", "所属公司", "项目类型", "项目类型", "项目情况",
                       "开标时间", "项目名称", "甲方名称", "代理机构", "参与单位", "报名费（元）",
                       "打印费（元）", "投标保证金（元）"]
        bond_header = ["序号", "项目名称", "甲方名称", "投标保证金（元）", "缴纳日期", "备注"]

        # 年度项目表：放 2 行真实语义数据（保证金信号在 2026 年表）
        sheet_with_header(
            "2026年", year_header,
            [1, 2026, "9月", "总公司", "劳务外包", "公开", "已中标", datetime(2026, 9, 17),
             "成都市某医院后勤劳务外包服务项目", "成都市第一人民医院", "/", "总公司", 0, 0, 50000],
            [2, 2026, "9月", "总公司", "劳务外包", "公开", "未中标", datetime(2026, 9, 20),
             "德阳市环卫保洁劳务外包项目", "德阳市城市管理局", "/", "总公司", 0, 0, 30000],
        )
        # 保证金 Sheet：真实台账结构
        sheet_with_header(
            "26年投标保证金 ", bond_header,
            [1, "成都市某医院后勤劳务外包服务项目", "成都市第一人民医院", 50000,
             datetime(2026, 9, 12), "开标后5个工作日内退"],
            [2, "德阳市环卫保洁劳务外包项目", "德阳市城市管理局", 30000,
             datetime(2026, 9, 14), None],
        )
        # 其余必需 Sheet 用占位结构补齐（ensure_required_sheets 不会重复建已存在的）
        self.ensure_required_sheets(wb, "bid_total_ledger", keep={
            "2025年": year_header,
            "25年投标保证金": bond_header,
        })
        wb.save(path)
        return path

    def make_corrupted(self, name: str = "损坏文件.xlsx") -> Path:
        path = self.case_dir / name
        path.write_bytes(b"not a zip at all" * 20)
        return path

    def make_encrypted(self, name: str = "加密文件.xlsx") -> Path:
        path = self.case_dir / name
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1024)
        return path

    def connector_for(self, source_id: str, path: Path, **kwargs) -> ExcelConnector:
        """按真实配置（表头行 / Sheet 级覆盖 / 字段映射）构造 Connector，测试即等于真跑。"""
        source = next((item for item in self.mapping.sources if item.source_id == source_id), None)
        params: dict = {
            "source_id": source_id,
            "name": source.name if source else source_id,
            "file_path": path,
            "field_mapping": source.fields if source else {},
            "parsing": self.mapping.parsing,
        }
        if source is not None:
            params["header_row"] = source.header_row if source.header_row_confirmed else None
            params["sheet_header_rows"] = source.sheet_header_rows
        params.update(kwargs)
        return ExcelConnector(**params)


class TestCellNormalization(ExcelConnectorTestBase):
    def test_datetime_gets_timezone(self):
        cell = normalize_cell(datetime(2026, 9, 1))
        self.assertEqual(cell.kind, "datetime")
        self.assertIsNotNone(cell.value.tzinfo)

    def test_date_text_parsed(self):
        parsed = parse_date_text("2026年9月28日")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 9, 28))

    def test_non_date_text_not_guessed(self):
        # 「报名截止时间」这类文本不是日期，绝不能瞎解析
        self.assertIsNone(parse_date_text("见招标文件"))

    def test_money_with_unit(self):
        self.assertEqual(parse_money_text("12.5万元"), 125000.0)
        self.assertEqual(parse_money_text("1,200,000.00"), 1200000.0)

    def test_number_stays_number(self):
        cell = normalize_cell(12000000)
        self.assertEqual(cell.kind, "number")
        self.assertEqual(cell.value, 12000000)

    def test_excel_serial_recovered_only_in_sane_range(self):
        """真表里日期列常是裸序列号（单元格没有日期格式）。"""
        recovered = excel_serial_to_datetime(46023)
        self.assertIsNotNone(recovered)
        self.assertEqual((recovered.year, recovered.month, recovered.day), (2026, 1, 1))
        self.assertIsNotNone(recovered.tzinfo)
        # 超出合理年份区间不得当成日期
        self.assertIsNone(excel_serial_to_datetime(12))
        self.assertIsNone(excel_serial_to_datetime(999999))
        self.assertIsNone(excel_serial_to_datetime(True))

    def test_strict_money_rejects_descriptive_text(self):
        """含拆分说明的长文本绝不硬抠数字当金额。"""
        descriptive = "1208.289898万元/年(不含税价)，其中不可竞争费用为1198.407898万元/年(不含税)"
        self.assertIsNone(parse_money_text(descriptive, strict=True))
        self.assertIsNotNone(parse_money_text(descriptive))  # 非严格模式仍可给出候选值
        self.assertEqual(parse_money_text("3.3万元", strict=True), 33000.0)
        self.assertEqual(parse_money_text("1,200,000.00", strict=True), 1200000.0)

    def test_null_tokens_treated_as_missing(self):
        self.assertTrue(is_null_token("/"))
        self.assertTrue(is_null_token("无"))
        self.assertFalse(is_null_token("120万元"))


class TestExcelConnectorSourcePointer(ExcelConnectorTestBase):
    def test_source_pointer_fields_fully_populated(self):
        """§4：每条记录必须产出填满字段的 SourcePointer。"""
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(len(records), 3)  # 空行 + 内嵌重复表头行都被跳过

        pointer = records[0].pointer
        self.assertEqual(pointer.source_type, SourceType.sheet)
        self.assertEqual(pointer.trust_level, TrustLevel.internal)
        self.assertEqual(pointer.file_name, path.name)
        self.assertEqual(pointer.sheet_name, "1月")
        self.assertEqual(pointer.row_id, "3")
        # 第 19 列是「服务开始日期」（S）：有值则范围到 S
        self.assertEqual(pointer.cell_range, "A3:S3")
        self.assertTrue(pointer.original_text and "成都市某医院后勤劳务外包服务项目" in pointer.original_text)
        self.assertEqual(len(pointer.content_hash or ""), 64)
        self.assertIsNotNone(pointer.captured_at)
        self.assertEqual(pointer.published_at.year, 2026)  # 来自「招标时间」
        self.assertIsNotNone(pointer.file_id)
        # row_id 存纯行号，location_label 渲染成「第3行」
        self.assertIn("第3行", SourcePointerService.location_label(pointer))
        self.assertNotIn("第第", SourcePointerService.location_label(pointer))
        # 空行（R4）与内嵌重复表头行（R6）不能导致行号漂移
        self.assertEqual([record.row_number for record in records], [3, 5, 7])
        # 第 2 条记录最后一个有值的列是「中标单位」→ 单元格范围到 Q 列
        self.assertEqual(records[1].pointer.cell_range, "A5:Q5")

    def test_repeated_header_row_is_skipped_visibly(self):
        """真表把表头整行复制到了数据中间 → 必须跳过，且跳过行为可见。"""
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertNotIn("项目名称", [record.item.fields.get("项目名称") for record in records])
        self.assertEqual(connector.last_repeated_header_rows, [("1月", 6)])

    def test_every_record_has_its_own_pointer(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        pointer_ids = {record.pointer.id for record in records}
        self.assertEqual(len(pointer_ids), len(records))

    def test_original_table_is_never_written(self):
        """§14：只读接入，绝不修改业务原表。"""
        path = self.make_blind_bid()
        before = file_digest(path)
        mtime_before = path.stat().st_mtime_ns
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月", "2026年未参与"])
        connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(file_digest(path), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)


class TestExcelConnectorParsing(ExcelConnectorTestBase):
    def test_money_column_normalized_to_yuan(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(records[0].item.fields["招标金额"], 12000000)   # 纯数字
        self.assertEqual(records[1].item.fields["招标金额"], 8800000.0)  # 880万元 → 元

    def test_deadline_is_datetime(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertIsInstance(records[0].item.fields["报名截止时间"], datetime)

    def test_merged_header_fill_forward_and_dedupe(self):
        """合并表头：空表头向前继承；重名自动加后缀。"""
        path = self.make_bid_plan()
        connector = self.connector_for("bid_plan_2026", path, sheets=["两行表头"], header_rows=[1, 2])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(len(records), 1)
        headers = records[0].header
        self.assertEqual(headers[0], "项目名称")
        self.assertEqual(headers[1], "招标人")
        self.assertEqual(headers[2], "地区")  # 第二行补齐
        self.assertNotIn("", headers)

    def test_auto_skips_empty_rows_without_drift(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual([record.row_number for record in records], [3, 5, 7])

    def test_content_hash_stable_and_change_sensitive(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        first = connector.fetch_records(ConnectorContext(limit=0))
        second = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(first[0].pointer.content_hash, second[0].pointer.content_hash)

        # 改一个单元格 → 该行 hash 必须变化（变化检测有效）
        wb = Workbook()
        wb.active.title = "1月"
        wb.active.append(["序号", "项目名称", "甲方名称"])
        wb.active.append([1, "成都市某医院后勤劳务外包服务项目", "成都市第一人民医院"])
        changed_path = self.case_dir / "changed.xlsx"
        wb.save(changed_path)
        changed = ExcelConnector(
            source_id="blind_bid_2026", name="t", file_path=changed_path,
            field_mapping={"项目名称": {"target": "projects.name"}, "甲方名称": {"target": "projects.client_name"}},
        ).fetch_records(ConnectorContext(limit=0))
        self.assertNotEqual(first[0].pointer.content_hash, changed[0].pointer.content_hash)

    def test_limit_truncation_is_flagged(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["1月"])
        records = connector.fetch_records(ConnectorContext(limit=1))
        self.assertEqual(len(records), 1)
        self.assertTrue(connector.last_truncated)  # 截断必须显式标记，不静默

    def test_sheet_header_rows_override(self):
        """同一个文件里不同 Sheet 的表头行不同（真表确实如此）。"""
        path = self.make_blind_bid()
        connector = self.connector_for(
            "blind_bid_2026", path, sheets=["1月", "2026年未参与"], sheet_header_rows={"1月": 1}
        )
        connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(connector.last_header_rows_by_sheet["1月"], 1)
        self.assertEqual(connector.last_header_rows_by_sheet["2026年未参与"], 2)

    def test_sheet_fields_override(self):
        """同一个列名在不同 Sheet 语义不同 → 按 Sheet 覆盖字段定义。"""
        fields_2026 = import_excel.SourceConfig(
            source_id="s", name="s", file="f", sheet=None, sheets=[], header_row=2,
            header_row_confirmed=True, entity_type_hint=None, status="", record_type="project",
            sheet_fields={"A": {"项目类型": {"target": "meta.procurement_method"}}},
            fields={"项目类型": {"target": "meta.business_category"}},
        )
        self.assertEqual(fields_2026.fields_for("A")["项目类型"]["target"], "meta.procurement_method")
        self.assertEqual(fields_2026.fields_for("B")["项目类型"]["target"], "meta.business_category")


class TestRealWorldQuirks(ExcelConnectorTestBase):
    """真实业务表的结构怪癖：合并单元格、幽灵列、标题行、纵向合并的区域列。"""

    def make_merged_sheet(self) -> Path:
        path = self.case_dir / "合并表头与幽灵列.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        ws["A1"] = "重点关注企业名单"
        ws.merge_cells("A1:C1")            # 横向合并的标题行
        ws.append([])                       # 第2行留空占位（下面手工写入表头）
        ws["A2"], ws["B2"], ws["C2"] = "序号", "区域", "企业名称"
        ws.merge_cells("B2:C2")            # 横向合并的表头：C 应继承 B 的列名
        rows = [("宜宾", "宜宾美华康养服务有限公司"), ("宜宾", "宜宾凯翼汽车有限公司"),
                ("龙泉", "成都某某物流有限公司")]
        for offset, (region, company) in enumerate(rows):
            row = 3 + offset
            ws.cell(row=row, column=1, value=offset + 1)
            # 区域只在分组首行写一次（Excel 里靠合并单元格表达分组）
            ws.cell(row=row, column=2, value=region if offset in (0, 2) else None)
            ws.cell(row=row, column=3, value=company)
        ws.merge_cells("B3:B4")            # 纵向合并：区域只在第3行有值
        ws["Z1"] = None                    # 触发 dimension 虚高（幽灵列）
        wb.save(path)
        return path

    def test_merged_ranges_read_from_xlsx(self):
        path = self.make_merged_sheet()
        merged = read_merged_ranges(path)
        self.assertIn("S", merged)
        refs = {(r.min_row, r.min_col, r.max_row, r.max_col) for r in merged["S"]}
        self.assertIn((1, 1, 1, 3), refs)   # A1:C1
        self.assertIn((2, 2, 2, 3), refs)   # B2:C2
        self.assertIn((3, 2, 4, 2), refs)   # B3:B4

    def test_horizontal_merge_header_inherits_anchor_name(self):
        path = self.make_merged_sheet()
        connector = ExcelConnector(source_id="t", name="t", file_path=path, header_row=2)
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(records[0].header, ["序号", "区域", "区域_2"])

    def test_vertical_merge_value_filled_down(self):
        """B3:B4 的区域只写在第3行，第4行必须被填充，否则区域缺失。"""
        path = self.make_merged_sheet()
        connector = ExcelConnector(source_id="t", name="t", file_path=path, header_row=2)
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].cells["区域"].text, "宜宾")
        self.assertEqual(records[1].cells["区域"].text, "宜宾")  # 向下填充
        self.assertEqual(records[2].cells["区域"].text, "龙泉")
        self.assertGreater(connector.last_merged_filled, 0)

    def test_trailing_empty_columns_trimmed(self):
        path = self.make_merged_sheet()
        connector = ExcelConnector(source_id="t", name="t", file_path=path, header_row=2)
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(len(records[0].header), 3)  # 幽灵列 Z 不产生字段

    def test_title_row_not_mistaken_for_header(self):
        """第1行是标题（只有1个单元格），表头应落在第2行。"""
        path = self.case_dir / "标题行.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        ws["A1"] = "2026年盲投项目汇总"
        ws["A2"], ws["B2"], ws["C2"] = "序号", "项目名称", "甲方名称"
        ws["A3"], ws["B3"], ws["C3"] = 1, "某项目", "某甲方"
        wb.save(path)
        connector = ExcelConnector(source_id="t", name="t", file_path=path)
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(records[0].header[:3], ["序号", "项目名称", "甲方名称"])
        self.assertEqual(records[0].row_number, 3)

    def test_descriptive_amount_kept_as_text_for_human(self):
        """真表「招标金额」常是长描述 → 保留原文并标记，不硬抠成数字。"""
        path = self.case_dir / "金额描述.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        ws.append(["项目名称", "招标金额"])
        ws.append(["某项目", "1208.289898万元/年(不含税价)，其中不可竞争费用为1198.407898万元/年(不含税)"])
        wb.save(path)
        connector = ExcelConnector(
            source_id="t", name="t", file_path=path,
            field_mapping={"项目名称": {"target": "projects.name"},
                           "招标金额": {"target": "projects.budget"}},
        )
        record = connector.fetch_records(ConnectorContext(limit=0))[0]
        self.assertIsInstance(record.item.fields["招标金额"], str)
        self.assertIn("无法可靠解析", record.item.raw["field_notes"]["招标金额"])

    def test_slash_in_date_column_becomes_missing(self):
        path = self.case_dir / "斜杠日期.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        ws.append(["项目名称", "开标时间"])
        ws.append(["某项目", "/"])
        wb.save(path)
        connector = ExcelConnector(
            source_id="t", name="t", file_path=path,
            field_mapping={"项目名称": {"target": "projects.name"}, "开标时间": {"target": "meta.open_bid_at"}},
        )
        record = connector.fetch_records(ConnectorContext(limit=0))[0]
        self.assertNotIn("开标时间", record.item.fields)

    def test_trim_trailing_empty_columns_helper(self):
        rows = [[normalize_cell("a"), normalize_cell(None), normalize_cell(None)]]
        self.assertEqual(trim_trailing_empty_columns(rows), 1)


class TestExcelConnectorFailureVisibility(ExcelConnectorTestBase):
    def test_corrupted_file_reports_failure(self):
        path = self.make_corrupted()
        connector = self.connector_for("blind_bid_2026", path)
        records = connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(records, [])
        health = connector.health()
        self.assertTrue(health.human_action_required)
        self.assertIsNotNone(health.last_error)
        self.assertEqual(health.consecutive_failures, 1)
        self.assertEqual(health.items_found, 0)
        self.assertIsNone(health.last_success_at)
        # 连续失败要累加
        connector.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(connector.health().consecutive_failures, 2)

    def test_encrypted_file_reports_failure(self):
        path = self.make_encrypted()
        connector = self.connector_for("blind_bid_2026", path)
        connector.fetch_records(ConnectorContext(limit=0))
        health = connector.health()
        self.assertTrue(health.human_action_required)
        self.assertIn("加密", health.last_error or "")

    def test_missing_file_reports_failure(self):
        connector = self.connector_for("blind_bid_2026", self.case_dir / "不存在.xlsx")
        connector.fetch_records(ConnectorContext(limit=0))
        health = connector.health()
        self.assertTrue(health.human_action_required)
        self.assertIn("文件不存在", health.last_error or "")

    def test_missing_sheet_reports_failure(self):
        path = self.make_blind_bid()
        connector = self.connector_for("blind_bid_2026", path, sheets=["不存在的Sheet"])
        connector.fetch_records(ConnectorContext(limit=0))
        health = connector.health()
        self.assertTrue(health.human_action_required)
        self.assertIn("缺少配置的 Sheet", health.last_error or "")

    def test_strict_mode_raises(self):
        path = self.make_corrupted()
        connector = self.connector_for("blind_bid_2026", path, strict=True)
        with self.assertRaises(Exception):
            connector.fetch_records(ConnectorContext(limit=0))

    def test_editor_lock_file_reports_failure(self):
        """存在 ~$ 锁文件（有人正在编辑）时必须报失败，不能读中间态数据。"""
        path = self.make_blind_bid()
        lock = path.parent / f"~${path.name}"
        lock.write_bytes(b"lock")
        try:
            connector = self.connector_for("blind_bid_2026", path)
            records = connector.fetch_records(ConnectorContext(limit=0))
            self.assertEqual(records, [])
            health = connector.health()
            self.assertTrue(health.human_action_required)
            self.assertIn("锁文件", health.last_error or "")
            self.assertIn("正在被编辑", health.last_error or "")
        finally:
            lock.unlink(missing_ok=True)
        # 锁文件移除后恢复正常
        connector = self.connector_for("blind_bid_2026", path)
        self.assertGreater(len(connector.fetch_records(ConnectorContext(limit=0))), 0)

    def test_long_name_lock_file_matched_by_prefix(self):
        """Excel 会截断超长文件名的锁文件，仍须识别出来。"""
        path = self.make_blind_bid(name="一个非常长的业务表格文件名用于测试锁文件截断场景2026.xlsx")
        lock = path.parent / "~$一个非常长的业务表.xlsx"
        lock.write_bytes(b"lock")
        try:
            self.assertIsNotNone(find_lock_file(path))
        finally:
            lock.unlink(missing_ok=True)

    def test_success_resets_failure_counter(self):
        missing = self.connector_for("blind_bid_2026", self.case_dir / "不存在.xlsx")
        missing.fetch_records(ConnectorContext(limit=0))
        self.assertEqual(missing.health().consecutive_failures, 1)
        # sheets 参数用配置里真实存在的 Sheet（旧值「2026年盲投」已废弃）
        good = self.connector_for("blind_bid_2026", self.make_blind_bid(), sheets=["1月"])
        good.fetch_records(ConnectorContext(limit=0))
        health = good.health()
        self.assertEqual(health.consecutive_failures, 0)
        self.assertFalse(health.human_action_required)
        self.assertIsNotNone(health.last_success_at)
        # 1月 Sheet 夹具有 3 条有效数据行（空行与内嵌表头行必须被跳过）
        self.assertEqual(health.items_found, 3)


class TestDataDictionary(ExcelConnectorTestBase):
    def test_dictionary_and_mapping_outputs(self):
        self.make_blind_bid()
        self.make_bid_plan()
        self.make_competitor()
        self.make_channel()
        self.make_total_ledger()
        self.make_corrupted()
        out_md = self.case_dir / "DATA_DICTIONARY.md"
        out_csv = self.case_dir / "FIELD_MAPPING.csv"
        with redirect_stdout(io.StringIO()):
            code = build_data_dictionary.main([
                "--input", str(self.case_dir),
                "--out", str(out_md),
                "--mapping-out", str(out_csv),
                "--mapping", str(MAPPING_PATH),
            ])
        self.assertEqual(code, 0)
        self.assertTrue(out_md.exists() and out_csv.exists())

        markdown = out_md.read_text(encoding="utf-8")
        # ①Sheet ②字段（旧值「2026年盲投」是废弃 Sheet 名；现夹具 Sheet 为 1月/2026年未参与 等）
        self.assertIn("项目名称", markdown)
        self.assertIn("2026年", markdown)
        # ③④⑤⑥ 无法自动推断 → 必须标待业务确认，不得编造
        self.assertIn("待业务确认", markdown)
        # ⑦ 事实 / 人工判断，带 auto_inferred 供复核
        self.assertIn("auto_inferred", markdown)
        # ⑧ ID 与 Source Pointer 规则
        self.assertIn("bidos_proj_", markdown)
        self.assertIn("bidos_ent_", markdown)
        # 失败必须可见
        self.assertIn("读取失败清单", markdown)
        self.assertIn("损坏文件.xlsx", markdown)

        csv_text = out_csv.read_text(encoding="utf-8-sig")
        header = csv_text.splitlines()[0]
        for column in ("meaning", "owner", "original_source", "may_change", "field_kind", "target_mapping"):
            self.assertIn(column, header)
        # 人工判断字段必须被标 human（真表列名几经变更：旧「是否参与」→ 现
        # 「参与单位/项目情况/备注」，启发式按词根命中，不锁死具体列名）
        human_lines = [
            line for line in csv_text.splitlines()
            if ",human," in line or line.rstrip().endswith(",human")
        ]
        self.assertTrue(human_lines, "CSV 中必须存在被判定为 human 的人工判断字段")


class TestImportPlan(ExcelConnectorTestBase):
    def build_full_plan(self):
        self.make_blind_bid()
        self.make_bid_plan()
        self.make_competitor()
        self.make_channel()
        self.make_total_ledger()
        files = import_excel.discover_files([str(self.case_dir)])
        return import_excel.build_plan(self.mapping, files, organization_id="org_test", limit=0)

    def test_plan_is_deterministic_and_idempotent(self):
        """同一份输入跑两次，确定性 hash ID 必须完全一致（可重跑幂等）。"""
        first = self.build_full_plan()
        second = self.build_full_plan()
        first_ids = [(write.kind, write.id) for write in first.writes]
        second_ids = [(write.kind, write.id) for write in second.writes]
        self.assertEqual(first_ids, second_ids)
        project_ids = [write.id for write in first.writes if write.kind == "project"]
        self.assertEqual(len(project_ids), len(set(project_ids)))  # 不产生重复项目
        for project_id in project_ids:
            self.assertTrue(project_id.startswith("bidos_proj_"))

    def test_every_imported_row_has_source_pointer(self):
        plan = self.build_full_plan()
        pointers = [write for write in plan.writes if write.kind == "source_pointer"]
        objects = [write for write in plan.writes if write.kind == "source_object"]
        self.assertEqual(len(pointers), len(objects))
        # 旧夹具是 4 源 8 行；纳入投标项目汇总9.17.xlsx（bid_total_ledger）后，
        # 占位 Sheet 的表头行也会计入。锁下界（不锁死上限，映射随真表更新而变）。
        self.assertGreaterEqual(len(pointers), 8)
        for write in pointers:
            self.assertEqual(write.payload["source_type"], "sheet")
            self.assertEqual(write.payload["trust_level"], "internal")
            self.assertTrue(write.payload["sheet_name"])
            self.assertTrue(write.payload["row_id"])
            self.assertTrue(write.payload["cell_range"])
            self.assertEqual(len(write.payload["metadata"]["content_hash"]), 64)

    def test_estimated_end_date_marked_estimated(self):
        """§2.6：只有服务期限推算出来的结束日期才写，且必须 estimated。"""
        plan = self.build_full_plan()
        candidates = [
            write for write in plan.writes
            if write.kind == "project" and write.payload["name"] == "成都市某医院后勤劳务外包服务项目"
        ]
        self.assertTrue(candidates)
        project = next(
            (w for w in candidates if w.payload.get("project_end_date_kind") == "estimated"),
            candidates[0],
        )
        self.assertEqual(project.payload["project_end_date_kind"], "estimated")
        self.assertEqual(project.payload["project_end_at"], datetime(2028, 11, 1, tzinfo=project.payload["project_end_at"].tzinfo))
        self.assertIn("服务期限", project.payload["metadata"]["project_end_at_source"])

    def test_no_fabricated_end_date_and_human_review(self):
        """结束日期推算不出=留空（kind=None/合法枚举），且不再生成人工待办。

        2026-09-30 用户裁定：结束日期留空不待办（到期提醒自然跳过没有结束日期
        的项目），导入期不再为缺结束日期/结果无法归一化刷 HUMAN_REVIEW_REQUIRED。
        """
        plan = self.build_full_plan()
        for write in plan.writes:
            if write.kind == "project":
                kind = write.payload.get("project_end_date_kind")
                self.assertIn(kind, (None, "exact", "estimated", "manual"))
        events = [write for write in plan.writes if write.kind == "event"]
        self.assertFalse(
            any(
                write.payload["event_type"] == "HUMAN_REVIEW_REQUIRED"
                and str(write.payload.get("title", "")).startswith("缺少项目结束日期")
                for write in events
            ),
            "缺结束日期不应再生成人工待办（留空即可）",
        )

    def test_bond_ledger_requires_real_bond_signal(self):
        """竞对表的「中标时间」不得凭空造出保证金台账。"""
        plan = self.build_full_plan()
        bonds = [write for write in plan.writes if write.kind == "bid_bond"]
        # 盲投 2 + 投标计划 3 = 5 是下界；26年投标保证金 Sheet 额外贡献真实台账行
        self.assertGreaterEqual(len(bonds), 5)
        for write in bonds:
            self.assertIn(write.payload["status"], {"未缴纳", "已缴纳", "待退回", "逾期未退", "已退回", "无需退回"})

    def test_source_failure_produces_visible_event(self):
        """来源文件缺失 → SOURCE_FAILED 事件 + 报告 health_ok=False。

        2026-09-28 契约扩展：带 optional: true 的来源（如意向名单，常与主台账
        不同目录）在单目录联合导入时按约定 SKIP（health_ok=True、无事件）；
        其余来源缺失仍是生产事故，必须失败可见。
        """
        plan = import_excel.build_plan(self.mapping, {}, organization_id="org_test", limit=0)
        failed_events = [
            write for write in plan.writes
            if write.kind == "event" and write.payload["event_type"] == "SOURCE_FAILED"
        ]
        required = [s for s in self.mapping.sources if not s.optional]
        self.assertEqual(len(failed_events), len(required))
        for report in plan.reports:
            source_cfg = next(s for s in self.mapping.sources if s.source_id == report.source_id)
            self.assertEqual(not report.health_ok, not source_cfg.optional)

    def test_field_conflict_is_not_silently_overwritten(self):
        """FAILURE_MODES §3：两个来源同一字段不同值 → DATA_CONFLICT，跳过写入。"""
        file_a = self.case_dir / "source_a.xlsx"
        file_b = self.case_dir / "source_b.xlsx"
        for path, budget in ((file_a, 1000000), (file_b, 2000000)):
            wb = Workbook()
            ws = wb.active
            ws.title = "S"
            ws.append(["项目名称", "招标人", "预算金额"])
            ws.append(["冲突测试项目", "测试甲方", budget])
            wb.save(path)

        def source(source_id: str, file_name: str):
            return import_excel.SourceConfig(
                source_id=source_id, name=source_id, file=file_name, sheet="S", sheets=[],
                header_row=1, header_row_confirmed=True,
                entity_type_hint="client", status="test", record_type="project",
                fields={
                    "项目名称": {"target": "projects.name", "required": True},
                    "招标人": {"target": "projects.client_name", "entity": "client"},
                    "预算金额": {"target": "projects.budget"},
                },
            )

        mapping = import_excel.MappingConfig(
            raw={},
            sources=[source("src_a", file_a.name), source("src_b", file_b.name)],
            parsing={},
            pointer={},
            strip_suffixes=[],
        )
        plan = import_excel.build_plan(
            mapping, {file_a.name: file_a, file_b.name: file_b}, organization_id="org_test", limit=0
        )
        conflicts = [
            write for write in plan.writes
            if write.kind == "event" and write.payload["event_type"] == "DATA_CONFLICT"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("budget", conflicts[0].payload["metadata"]["field"])

    def test_entity_only_source_does_not_force_project(self):
        """渠道表/重点关注企业清单只产实体，不硬套项目主表。"""
        plan = self.build_full_plan()
        channel_objects = [
            write for write in plan.writes
            if write.kind == "source_object" and write.payload["raw_metadata"]["file_name"].startswith("盲投渠道表")
        ]
        self.assertGreaterEqual(len(channel_objects), 1)
        channel_pointers = [
            write for write in plan.writes
            if write.kind == "source_pointer" and write.payload["file_name"].startswith("盲投渠道表")
        ]
        self.assertGreaterEqual(len(channel_pointers), 1)
        # 红线：渠道/重点企业来源的任何记录都不得硬套到项目上
        for pointer in channel_pointers:
            self.assertIsNone(pointer.payload["project_id"])
        entities = [write for write in plan.writes if write.kind == "entity"]
        self.assertTrue(any(write.payload["canonical_name"] == "成都市第一人民医院" for write in entities))

    def test_dry_run_prints_plan_without_db(self):
        self.make_blind_bid()
        self.make_bid_plan()
        self.make_competitor()
        self.make_channel()
        self.make_total_ledger()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = import_excel.main([
                "--input", str(self.case_dir), "--dry-run", "--mapping", str(MAPPING_PATH), "--show", "5",
            ])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[DRY-RUN] 未写入数据库", output)
        self.assertIn("来源健康", output)
        self.assertIn("bidos_proj_", output)

    def test_missing_source_file_makes_exit_code_visible(self):
        """来源缺失必须让脚本以非 0 退出，不能报“成功”。"""
        self.make_blind_bid()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = import_excel.main([
                "--input", str(self.case_dir), "--dry-run", "--mapping", str(MAPPING_PATH),
            ])
        self.assertEqual(code, 1)
        self.assertIn("SOURCE_FAILED", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
