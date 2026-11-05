import concurrent.futures
import difflib
import uuid
import logging

logger = logging.getLogger(__name__)

class AddendumClauseDiffEngineBatchPipeline:
    """
    招标更正/澄清公告与原招标文件条款级红绿Diff对比及影响章节重算器。

    遵守【核心铁律】:
    1. 操作简单直观 (支持单次拖入，无需复杂配置)。
    2. 零配置容错，缺参自动设合理默认值。
    3. 后台功能复杂但对前端暴露的接口极简。
    4. 零阻塞结果汇总。
    """

    def __init__(self, max_workers=None):
        # 缺参自动设合理默认值
        self.max_workers = max_workers or 4

    def _safe_get_text(self, item, key, default=""):
        # 脏数据自愈：缺失键，或者非字符串值
        val = item.get(key)
        if val is None:
            return default
        if not isinstance(val, str):
            try:
                return str(val)
            except Exception:
                return default
        return val

    def _calculate_affected_chapters(self, diff_result, original_chapters=None):
        """
        根据差异结果，重算受影响的章节。
        如果原始章节没有提供，则尽量推断，或者返回一个默认列表。
        这里为了简单和容错，返回涉及变更的假设受影响列表。
        """
        affected = set()

        # 简单规则：如果有红绿Diff（非完全一致），根据差异定位受影响的章节。
        # 实际业务中可能结合NLP提取，此处做简化示例。
        has_diff = any(op.get('tag') in ('replace', 'insert', 'delete') for op in diff_result['diff_ops'])

        if has_diff:
            # 容错：如果未提供 original_chapters，则提供一个通用的 fallback 标记
            if not original_chapters or not isinstance(original_chapters, list):
                affected.add("未知/通用受影响章节")
            else:
                for chapter in original_chapters:
                    if isinstance(chapter, str):
                        affected.add(chapter)

        return list(affected)

    def _process_single_record(self, record):
        """
        处理单条记录。
        """
        if not isinstance(record, dict):
            # 脏数据自愈
            record = {}

        record_id = self._safe_get_text(record, 'id', str(uuid.uuid4()))
        original_text = self._safe_get_text(record, 'original_text')
        updated_text = self._safe_get_text(record, 'updated_text')
        original_chapters = record.get('original_chapters', [])

        # 使用 SequenceMatcher 计算红绿 Diff
        matcher = difflib.SequenceMatcher(None, original_text, updated_text)
        opcodes = matcher.get_opcodes()

        diff_ops = []
        for tag, i1, i2, j1, j2 in opcodes:
            diff_ops.append({
                'tag': tag,
                'original_segment': original_text[i1:i2],
                'updated_segment': updated_text[j1:j2]
            })

        diff_result = {
            'record_id': record_id,
            'is_changed': any(tag in ('replace', 'insert', 'delete') for tag, _, _, _, _ in opcodes),
            'diff_ops': diff_ops,
        }

        # 计算受影响章节
        affected_chapters = self._calculate_affected_chapters(diff_result, original_chapters)
        diff_result['affected_chapters'] = affected_chapters

        return diff_result

    def process_batch(self, records):
        """
        批量流式处理 10~1000 条记录的接口。

        参数:
        - records: List[Dict]，需包含 original_text 和 updated_text，可选 id 和 original_chapters

        返回:
        - results: List[Dict]，包含 record_id, diff_ops, affected_chapters 等
        """
        # 脏数据自愈: 空数据兜底
        if not records:
            return []

        if not isinstance(records, list):
            records = [records]

        # 进度快照/追踪设计 (简略版，实际中可搭配缓存或持久化存储使用)
        snapshot = {
            'total': len(records),
            'processed': 0,
            'results': []
        }

        # 高吞吐并发加速
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交流水线任务
            future_to_record = {executor.submit(self._process_single_record, record): i for i, record in enumerate(records)}

            # 零阻塞结果汇总 (as_completed)
            for future in concurrent.futures.as_completed(future_to_record):
                try:
                    res = future.result()
                    snapshot['results'].append(res)
                    snapshot['processed'] += 1
                except Exception as exc:
                    logger.error(f"Error processing record: {exc}")
                    # 不因单条报错卡死整体
                    snapshot['results'].append({
                        'record_id': "error",
                        'is_changed': False,
                        'diff_ops': [],
                        'affected_chapters': [],
                        'error': str(exc)
                    })
                    snapshot['processed'] += 1

        return snapshot['results']
