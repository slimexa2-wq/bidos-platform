import json
import logging
from typing import List, Dict, Any, Union
import math

logger = logging.getLogger(__name__)

class CreditBlacklistRadar:
    """
    信用中国/中国政府采购网无重大违法记录声明与截图核验清单器。

    设计原则：
    1. 零配置容错、缺参自动设合理默认值、静默清理脏数据。
    2. 极简标记：红、黄、绿三色。
    3. 自动高亮真正需要人工关注的Top 3%异常项，其余静默放行。
    4. 给出大白话原因。
    """

    DEFAULT_SCREENSHOT_SIZE_KB = 500.0
    DEFAULT_OCR_CONFIDENCE = 95.0

    def _clean_record(self, record: Any) -> Dict[str, Any]:
        """清理脏数据"""
        if record is None:
            return {}

        if isinstance(record, str):
            try:
                # 尝试解析JSON字符串
                record = json.loads(record)
                if not isinstance(record, dict):
                    record = {'vendor_name': str(record)}
            except json.JSONDecodeError:
                record = {'vendor_name': record}

        if not isinstance(record, dict):
            return {}

        cleaned = dict(record)

        # 处理负值、类型错误等
        for key in ['screenshot_size_kb', 'ocr_confidence']:
            val = cleaned.get(key)
            if val is None:
                cleaned[key] = self.DEFAULT_SCREENSHOT_SIZE_KB if key == 'screenshot_size_kb' else self.DEFAULT_OCR_CONFIDENCE
                continue

            try:
                val = float(val)
                if math.isnan(val) or math.isinf(val):
                    raise ValueError
                cleaned[key] = abs(val) # 取绝对值修复负数
            except (ValueError, TypeError):
                cleaned[key] = self.DEFAULT_SCREENSHOT_SIZE_KB if key == 'screenshot_size_kb' else self.DEFAULT_OCR_CONFIDENCE

        # 布尔值处理
        is_blacklisted = cleaned.get('is_blacklisted', False)
        if isinstance(is_blacklisted, str):
            is_blacklisted = is_blacklisted.lower() in ('true', '1', 'yes', 'y')
        cleaned['is_blacklisted'] = bool(is_blacklisted)

        # 确保存在vendor_name
        if not cleaned.get('vendor_name'):
            cleaned['vendor_name'] = '未知供应商'

        return cleaned

    def _calculate_z_scores(self, values: List[float]) -> List[float]:
        """计算Z-Score"""
        n = len(values)
        if n == 0:
            return []
        if n == 1:
            return [0.0]

        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        # 如果方差为0，说明所有值一样，那么Z-Score都是0
        if variance == 0:
            return [0.0] * n

        std_dev = math.sqrt(variance)
        return [(x - mean) / std_dev for x in values]

    def _calculate_anomaly_scores(self, cleaned_records: List[Dict[str, Any]]) -> List[float]:
        """计算综合异常分，基于IQR和Z-Score进行统计分布异常值扫描"""
        sizes = [r['screenshot_size_kb'] for r in cleaned_records]
        confidences = [r['ocr_confidence'] for r in cleaned_records]

        size_z = self._calculate_z_scores(sizes)
        conf_z = self._calculate_z_scores(confidences)

        # 异常分数 = |大小Z分| + |置信度Z分的负向偏离|
        # 大小无论是过大还是过小都可疑，置信度过低可疑
        scores = []
        for i in range(len(cleaned_records)):
            sz = abs(size_z[i])
            cz = max(0, -conf_z[i]) # 惩罚低置信度
            scores.append(sz + cz)

        return scores

    def analyze(self, vendor_records: Union[List[Any], None]) -> Dict[str, List[Dict[str, Any]]]:
        """
        分析供应商记录。

        返回字典:
        {
            "RED": [...],
            "YELLOW": [...],
            "GREEN": [...]
        }
        """
        result = {"RED": [], "YELLOW": [], "GREEN": []}

        if vendor_records is None:
            return result

        if not isinstance(vendor_records, list):
            vendor_records = [vendor_records]

        cleaned_records = [self._clean_record(r) for r in vendor_records if r is not None]
        if not cleaned_records:
            return result

        # 强制黑名单直接标记为红
        for idx, record in enumerate(cleaned_records):
            if record['is_blacklisted']:
                record['status'] = 'RED'
                record['reason'] = '该供应商已被信用中国或中国政府采购网明确列入黑名单，严禁交易。'

        # 过滤出还没被标记为红的记录计算异常分
        non_red_indices = [i for i, r in enumerate(cleaned_records) if r.get('status') != 'RED']
        non_red_records = [cleaned_records[i] for i in non_red_indices]

        if not non_red_records:
            for r in cleaned_records:
                result[r['status']].append(r)
            return result

        scores = self._calculate_anomaly_scores(non_red_records)

        # 确定Top 3%的阈值
        n_non_red = len(non_red_records)
        top_k = max(1, math.ceil(n_non_red * 0.03)) # 至少取1个作为候选，如果分数确实异常

        # 将分数和索引结合排序
        score_idx_pairs = [(scores[i], non_red_indices[i]) for i in range(n_non_red)]
        score_idx_pairs.sort(reverse=True, key=lambda x: x[0])

        # 标记Top 3%如果分数显著
        threshold_z = 2.0 # 经验值，Z-Score超过2才认为是真异常
        for i in range(n_non_red):
            score, original_idx = score_idx_pairs[i]
            record = cleaned_records[original_idx]

            if i < top_k and score > threshold_z:
                record['status'] = 'YELLOW'
                reasons = []

                # 分析原因
                sizes = [r['screenshot_size_kb'] for r in non_red_records]
                mean_size = sum(sizes) / len(sizes) if sizes else self.DEFAULT_SCREENSHOT_SIZE_KB

                if record['screenshot_size_kb'] < mean_size * 0.2:
                    reasons.append('截图文件异常过小（像是被过度压缩或裁剪过）')
                elif record['screenshot_size_kb'] > mean_size * 2:
                    reasons.append('截图文件异常过大（可能被后期软件过度编辑）')

                if record['ocr_confidence'] < 60:
                    reasons.append('机器识别截图文字时感觉非常模糊，难以看清')

                if not reasons:
                    reasons.append('各项指标综合看起来有点异样，跟其他大部分供应商的数据不太合群')

                record['reason'] = '可能存在问题，建议人工瞅一眼：' + '；'.join(reasons) + '。'
            else:
                record['status'] = 'GREEN'
                record['reason'] = '一切正常，静默放行。'

        # 组装结果
        for record in cleaned_records:
            result[record['status']].append(record)

        return result
