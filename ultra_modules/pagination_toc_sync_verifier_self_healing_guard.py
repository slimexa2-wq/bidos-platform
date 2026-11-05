import uuid
import time
import copy

class PaginationTocSyncVerifierSelfHealingGuard:
    """
    标书终稿目录页码与正文实际页码偏移静默校验与修正提示器。
    支持异常输入静默自愈与5秒一键撤回(Undo)快照机制。
    极简（一键直达、零确认弹窗、零配置容错、缺参自动设合理默认值，严禁过度安全复杂化恶心用户）。
    """
    def __init__(self):
        # 存储快照数据：{snapshot_id: {'timestamp': float, 'data': dict}}
        self._snapshots = {}
        # 撤回时间窗口：5秒
        self._undo_window_seconds = 5.0

    def _heal_page_number(self, page_num):
        """静默自愈页码"""
        if page_num is None:
            return 1
        if isinstance(page_num, int):
            return max(1, page_num)
        if isinstance(page_num, str):
            try:
                num = int(page_num)
                return max(1, num)
            except ValueError:
                pass
        # 其他异常或无法解析的情况，默认兜底为1
        return 1

    def _heal_title(self, title):
        """静默自愈标题"""
        if title is None:
            return "Untitled"
        if not isinstance(title, str):
            return str(title)
        # 去除前后空白，为空则给默认值
        healed = title.strip()
        if not healed:
            return "Untitled"
        return healed

    def verify_and_heal(self, toc_entries=None, actual_pages=None):
        """
        校验并修正目录和正文的页码同步。
        :param toc_entries: 目录项列表，格式如 [{'title': 'Chapter 1', 'page': 1}, ...]
        :param actual_pages: 实际正文章节页码映射，格式如 {'Chapter 1': 2, ...}
        :return: {'status': 'success', 'healed_toc': list, 'snapshot_id': str}
        """
        # 缺参自动设合理默认值
        if toc_entries is None:
            toc_entries = []
        if actual_pages is None:
            actual_pages = {}

        # 防止非预期类型抛出异常
        if not isinstance(toc_entries, list):
            toc_entries = []
        if not isinstance(actual_pages, dict):
            actual_pages = {}

        # 保存原始状态作为快照
        snapshot_id = str(uuid.uuid4())
        self._snapshots[snapshot_id] = {
            'timestamp': time.time(),
            'toc_entries': copy.deepcopy(toc_entries),
        }

        healed_toc = []
        for entry in toc_entries:
            if not isinstance(entry, dict):
                continue

            title = self._heal_title(entry.get('title'))
            toc_page = self._heal_page_number(entry.get('page'))

            # 从实际页码中获取真实页码（静默自愈获取）
            actual_page_raw = actual_pages.get(title)
            if actual_page_raw is not None:
                actual_page = self._heal_page_number(actual_page_raw)
            else:
                # 实际页码中找不到，维持自愈后的目录页码
                actual_page = toc_page

            healed_toc.append({
                'title': title,
                'page': actual_page
            })

        return {
            'status': 'success',
            'healed_toc': healed_toc,
            'snapshot_id': snapshot_id
        }

    def undo(self, snapshot_id):
        """
        一键撤回(Undo)快照机制
        :param snapshot_id: 快照ID
        :return: {'status': 'success', 'restored_toc': list} 或 {'status': 'error', 'message': str, 'restored_toc': list}
        """
        if not snapshot_id or not isinstance(snapshot_id, str):
            return {'status': 'error', 'message': 'Invalid snapshot_id', 'restored_toc': []}

        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            return {'status': 'error', 'message': 'Snapshot not found', 'restored_toc': []}

        # 检查是否在5秒撤回窗口内
        current_time = time.time()
        if current_time - snapshot['timestamp'] > self._undo_window_seconds:
            # 即使超时，也不应该抛出异常，返回错误状态，静默容错兜底返回原状态（或空列表）
            return {'status': 'error', 'message': 'Undo window expired', 'restored_toc': snapshot['toc_entries']}

        # 恢复成功
        restored_toc = copy.deepcopy(snapshot['toc_entries'])
        return {'status': 'success', 'restored_toc': restored_toc}
