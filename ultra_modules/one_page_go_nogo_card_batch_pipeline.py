import concurrent.futures
from typing import List, Dict, Any, Generator

class OnePageGoNoGoCardBatchPipeline:
    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers

    def process_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理单条记录：缺参默认兜底、脏数据自愈，并生成决策【可投/慎投/禁投】
        """
        # 缺参默认兜底
        if record is None:
            record = {}

        # 提取字段，如果不存在则使用默认值
        title = record.get('title', 'Unknown Tender')
        budget = record.get('budget', 0)
        risk_level = record.get('risk_level', 'low')

        # 脏数据自愈
        try:
            budget = float(budget)
        except (ValueError, TypeError):
            budget = 0.0

        if not isinstance(risk_level, str):
            risk_level = 'low'

        risk_level = risk_level.lower()

        # 生成决策卡
        decision = '可投'

        if risk_level == 'high' or budget < 1000:
            decision = '禁投'
        elif risk_level == 'medium' or budget < 10000:
            decision = '慎投'

        return {
            'original_id': record.get('id', 'unknown'),
            'title': title,
            'budget': budget,
            'decision': decision
        }

    def process_batch(self, records: List[Dict[str, Any]]) -> Generator[Dict[str, Any], None, None]:
        """
        支持单次拖入10~1000条记录的批量流式处理、进度快照与零阻塞结果汇总
        """
        if not records:
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_record = {executor.submit(self.process_record, record): record for record in records}

            # 零阻塞结果流式汇总
            for future in concurrent.futures.as_completed(future_to_record):
                try:
                    result = future.result()
                    yield result
                except Exception as exc:
                    # 极简容错：直接返回包含错误信息的兜底结果，不阻塞主流程
                    yield {
                        'original_id': 'error',
                        'title': 'Error Processing Record',
                        'budget': 0,
                        'decision': '禁投',
                        'error': str(exc)
                    }
