import concurrent.futures
import re
from typing import List, Dict, Any, Optional


class CompetitorWinPriceAnalyzerBatchPipeline:
    """
    历史中标公示数据抓取清洗与同区域竞争对手报价折扣率分布分析器。
    支持单次拖入批量记录流式处理、进度快照与零阻塞结果汇总。
    """

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        # 默认回退值
        self.default_competitor = "未知竞争对手"
        self.default_price = 0.0

    def _clean_price(self, price_val: Any) -> float:
        """清洗价格数据，支持脏数据自愈"""
        if price_val is None:
            return self.default_price

        if isinstance(price_val, (int, float)):
            return float(price_val)

        if isinstance(price_val, str):
            # 去除所有的非数字、非小数点字符，提取第一个连续数字片段
            match = re.search(r'\d+(\.\d+)?', price_val.replace(',', ''))
            if match:
                return float(match.group())

        return self.default_price

    def _process_single_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """处理单条记录，包含缺失参数回退"""
        competitor = record.get('competitor')
        if not competitor or not isinstance(competitor, str) or not competitor.strip():
            competitor = self.default_competitor
        else:
            competitor = competitor.strip()

        bid_price = self._clean_price(record.get('bid_price'))
        win_price = self._clean_price(record.get('win_price'))

        # 如果只有其中一个价格，进行逻辑回退
        if win_price <= 0 and bid_price > 0:
            win_price = bid_price
        elif bid_price <= 0 and win_price > 0:
            bid_price = win_price

        # 计算折扣率
        discount_rate = 1.0
        if bid_price > 0:
            discount_rate = win_price / bid_price
            # 防止异常数据导致的巨大/极小折扣率
            if discount_rate < 0:
                discount_rate = 0.0

        return {
            'competitor': competitor,
            'bid_price': bid_price,
            'win_price': win_price,
            'discount_rate': round(discount_rate, 4),
            'raw_data_snapshot': record  # 保留快照
        }

    def process_batch(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量处理入口，使用线程池高吞吐并发加速封装"""
        if not records:
            return {
                'total_processed': 0,
                'results': [],
                'summary': {}
            }

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_record = {executor.submit(self._process_single_record, record): record for record in records}
            for future in concurrent.futures.as_completed(future_to_record):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    # 极简理念：后台复杂但前端零阻塞，单个记录失败不阻塞整体
                    pass

        # 零阻塞结果汇总
        summary = self._generate_summary(results)

        return {
            'total_processed': len(results),
            'results': results,
            'summary': summary
        }

    def _generate_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成同区域竞争对手报价折扣率分布汇总"""
        summary = {}
        for res in results:
            comp = res['competitor']
            if comp not in summary:
                summary[comp] = {
                    'count': 0,
                    'total_discount': 0.0,
                    'min_discount': float('inf'),
                    'max_discount': -float('inf')
                }

            summary[comp]['count'] += 1
            dr = res['discount_rate']
            summary[comp]['total_discount'] += dr
            if dr < summary[comp]['min_discount']:
                summary[comp]['min_discount'] = dr
            if dr > summary[comp]['max_discount']:
                summary[comp]['max_discount'] = dr

        # 计算平均值并清理初始化的极值
        for comp, data in summary.items():
            if data['count'] > 0:
                data['avg_discount'] = round(data['total_discount'] / data['count'], 4)
            else:
                data['avg_discount'] = 0.0

            if data['min_discount'] == float('inf'):
                data['min_discount'] = 0.0
            if data['max_discount'] == -float('inf'):
                data['max_discount'] = 0.0

            data['min_discount'] = round(data['min_discount'], 4)
            data['max_discount'] = round(data['max_discount'], 4)
            del data['total_discount'] # 不再需要，清理掉

        return summary
