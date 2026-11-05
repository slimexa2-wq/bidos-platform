import time
import uuid
import re

class SealSignaturePageLocator:
    """
    千页标书全文需盖公章/法人章/骑缝章/签字页码一键精准定位清单生成器
    支持异常输入静默自愈与5秒一键撤回(Undo)快照机制。
    """

    def __init__(self, default_config=None):
        if isinstance(default_config, dict):
            self.default_config = default_config
        else:
            self.default_config = {
                "official_seal_pages": [1, -1],  # 默认首页和尾页
                "legal_person_seal_pages": [-1], # 默认尾页
                "cross_page_seal": True,         # 默认全本骑缝章
                "signature_pages": [-1]          # 默认尾页
            }
        self.snapshots = {}
        self.UNDO_WINDOW_SECONDS = 5.0

    def _extract_number(self, val, default=1):
        if isinstance(val, bool):
            return 1 if val else 0
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            nums = re.findall(r'-?\d+', val)
            if nums:
                return int(nums[0])
        return default

    def _sanitize_pages(self, pages, total_pages):
        sanitized = []
        if not isinstance(pages, (list, tuple, set)):
            pages = [pages]

        for p in pages:
            p_int = self._extract_number(p, default=None)
            if p_int is None:
                continue

            # 支持倒数页码，例如 -1 表示最后一页
            if p_int < 0:
                p_int = total_pages + p_int + 1

            # 过滤超出范围的页码
            if 1 <= p_int <= total_pages:
                sanitized.append(p_int)

        # 去重并排序
        return sorted(list(set(sanitized)))

    def generate_locator_list(self, document_data, config=None):
        """
        生成盖章签字页码清单。
        自动兼容和修复脏数据，不抛出异常。
        """
        # 静默自愈解析文档数据
        total_pages = 1
        if isinstance(document_data, (int, float)):
            total_pages = int(document_data)
        elif isinstance(document_data, str):
            total_pages = self._extract_number(document_data, default=1)
        elif isinstance(document_data, dict):
            total_pages = self._extract_number(document_data.get("total_pages", 1), default=1)

        if total_pages < 1:
            total_pages = 1

        # 静默自愈解析配置数据
        if not isinstance(config, dict):
            config = {}

        merged_config = {**self.default_config}
        for k, v in config.items():
            if v is not None:
                merged_config[k] = v

        official = self._sanitize_pages(merged_config.get("official_seal_pages", [1, -1]), total_pages)
        legal = self._sanitize_pages(merged_config.get("legal_person_seal_pages", [-1]), total_pages)
        signature = self._sanitize_pages(merged_config.get("signature_pages", [-1]), total_pages)

        # 骑缝章逻辑（全本或按固定间隔）
        cross_page_seal_pages = []
        cp_config = merged_config.get("cross_page_seal", True)
        if isinstance(cp_config, bool):
            if cp_config:
                cross_page_seal_pages = list(range(1, total_pages + 1))
            else:
                cross_page_seal_pages = []
        elif isinstance(cp_config, int) and cp_config > 0:
            cross_page_seal_pages = list(range(1, total_pages + 1, cp_config))
        elif isinstance(cp_config, str):
            interval = self._extract_number(cp_config, default=0)
            if interval > 0:
                cross_page_seal_pages = list(range(1, total_pages + 1, interval))
            else:
                cross_page_seal_pages = list(range(1, total_pages + 1))
        else:
            # 兜底：生成所有页码
            cross_page_seal_pages = list(range(1, total_pages + 1))

        result = {
            "total_pages": total_pages,
            "official_seal_pages": official,
            "legal_person_seal_pages": legal,
            "cross_page_seal_pages": cross_page_seal_pages,
            "signature_pages": signature,
        }

        # 自动生成可一键撤回的快照
        snapshot_id = self.create_snapshot(result)
        result["_snapshot_id"] = snapshot_id
        return result

    def create_snapshot(self, data):
        """
        创建轻量快照，默认保留5秒
        """
        snapshot_id = str(uuid.uuid4())
        # 浅层深拷贝以避免引用污染
        snap_data = {k: (v.copy() if isinstance(v, list) else v) for k, v in data.items()}
        self.snapshots[snapshot_id] = {
            "timestamp": time.time(),
            "data": snap_data
        }

        # 顺便清理过期快照，避免内存泄漏，静默运行
        current_time = time.time()
        expired = [sid for sid, snap in self.snapshots.items() if current_time - snap["timestamp"] > self.UNDO_WINDOW_SECONDS]
        for sid in expired:
            del self.snapshots[sid]

        return snapshot_id

    def undo(self, snapshot_id):
        """
        5秒内一键撤回。若过期或不存在则静默返回 None。
        """
        if not snapshot_id or not isinstance(snapshot_id, str):
            return None

        if snapshot_id not in self.snapshots:
            return None

        snapshot = self.snapshots.pop(snapshot_id)
        if time.time() - snapshot["timestamp"] <= self.UNDO_WINDOW_SECONDS:
            return snapshot["data"]

        return None
