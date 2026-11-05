import re
import numpy as np
from typing import List, Dict, Any
from collections import Counter

class BidConsistencyAnomalyRadar:
    """
    跨章节一致性静默质检器 - 异常扫描与三色标记
    核心理念: 一键直达、零配置容错、缺参自动设合理默认值、极简白话输出。
    """

    def __init__(self):
        # Configuration for top 3% anomaly
        self.red_flag_percentile = 3.0

    def clean_amount(self, amount: Any) -> float:
        if amount is None:
            return 0.0
        try:
            if isinstance(amount, (int, float)):
                return float(amount)
            amount_str = str(amount).replace(',', '').replace('，', '').strip()
            matches = re.findall(r"[-+]?\d*\.\d+|\d+", amount_str)
            if matches:
                return float(matches[0])
            return 0.0
        except Exception:
            return 0.0

    def clean_days(self, days: Any) -> int:
        if days is None:
            return 0
        try:
            if isinstance(days, (int, float)):
                return int(days)
            days_str = str(days).replace(',', '').strip()
            matches = re.findall(r"[-+]?\d+", days_str)
            if matches:
                return int(matches[0])
            return 0
        except Exception:
            return 0

    def clean_string(self, val: Any, default: str = "Unknown") -> str:
        if val is None:
            return default
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ['none', 'null', 'nan']:
            return default
        return val_str

    def calculate_iqr_bounds(self, values: List[float]) -> tuple:
        if not values or len(values) < 2:
            return -float('inf'), float('inf')

        q1 = np.percentile(values, 25)
        q3 = np.percentile(values, 75)
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        return lower_bound, upper_bound

    def find_anomalies(self, values: List[float], lower_bound: float, upper_bound: float) -> List[int]:
        anomalies_indices = []
        for i, val in enumerate(values):
            if val < lower_bound or val > upper_bound:
                anomalies_indices.append(i)
        return anomalies_indices

    def scan_anomalies(self, data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Main method to scan for anomalies and flag them.
        """
        if not data_list or not isinstance(data_list, list):
            return {"status": "success", "message": "无数据", "results": []}

        # Data cleaning and prep
        cleaned_data = []
        amounts = []
        durations = []
        managers = []
        certs = []

        for idx, item in enumerate(data_list):
            if not isinstance(item, dict):
                item = {}

            amount = self.clean_amount(item.get('amount', 0))
            duration = self.clean_days(item.get('duration_days', 0))
            manager = self.clean_string(item.get('manager_name', 'Unknown'))
            cert = self.clean_string(item.get('cert_number', 'Unknown'))

            cleaned_data.append({
                'original_index': idx,
                'amount': amount,
                'duration_days': duration,
                'manager_name': manager,
                'cert_number': cert,
                'scores': {'amount': 0, 'duration': 0, 'manager': 0, 'cert': 0},
                'reasons': []
            })

            amounts.append(amount)
            durations.append(duration)
            managers.append(manager)
            certs.append(cert)

        # IQR for Numeric
        amount_lb, amount_ub = self.calculate_iqr_bounds(amounts)
        duration_lb, duration_ub = self.calculate_iqr_bounds([float(d) for d in durations])

        # Frequency for Strings
        manager_counts = Counter(managers)
        cert_counts = Counter(certs)

        total_items = len(cleaned_data)

        for item in cleaned_data:
            # Numeric Score
            if item['amount'] < amount_lb or item['amount'] > amount_ub:
                item['scores']['amount'] = 100
                item['reasons'].append(f"金额({item['amount']})偏离常规范围大")
            elif amount_lb != float('-inf') and amount_ub != amount_lb and ((item['amount'] - amount_lb) / (amount_ub - amount_lb + 1e-9) < 0.1 or (amount_ub - item['amount']) / (amount_ub - amount_lb + 1e-9) < 0.1):
                item['scores']['amount'] = 50

            if item['duration_days'] < duration_lb or item['duration_days'] > duration_ub:
                item['scores']['duration'] = 100
                item['reasons'].append(f"工期({item['duration_days']}天)偏离常规范围大")
            elif duration_lb != float('-inf') and duration_ub != duration_lb and ((item['duration_days'] - duration_lb) / (duration_ub - duration_lb + 1e-9) < 0.1 or (duration_ub - item['duration_days']) / (duration_ub - duration_lb + 1e-9) < 0.1):
                item['scores']['duration'] = 50

            # String Score (Anomaly if rare)
            mgr_freq = manager_counts[item['manager_name']] / total_items
            if mgr_freq < 0.1 and len(manager_counts) > 1:
                item['scores']['manager'] = 100
                item['reasons'].append(f"项目经理({item['manager_name']})非主流选项")
            elif mgr_freq < 0.3 and len(manager_counts) > 1:
                item['scores']['manager'] = 50

            cert_freq = cert_counts[item['cert_number']] / total_items
            if cert_freq < 0.1 and len(cert_counts) > 1:
                item['scores']['cert'] = 100
                item['reasons'].append(f"执业证号({item['cert_number']})非主流选项")
            elif cert_freq < 0.3 and len(cert_counts) > 1:
                item['scores']['cert'] = 50

            item['total_score'] = sum(item['scores'].values())

        # Determine Threshold for Top 3%
        if len(cleaned_data) > 0:
            scores = [item['total_score'] for item in cleaned_data]
            top_3_threshold = np.percentile(scores, 100 - self.red_flag_percentile)
        else:
            top_3_threshold = float('inf')

        # Ensure we only flag actually anomalous items if scores are 0
        if top_3_threshold == 0:
            top_3_threshold = 1 # Need at least some anomaly score to be red

        # Assign colors
        results = []
        for item in cleaned_data:
            if item['total_score'] >= top_3_threshold and item['total_score'] > 0:
                color = 'Red'
                if not item['reasons']:
                    reason = "综合得分较高，需复核"
                else:
                    reason = "；".join(item['reasons'])
            elif item['total_score'] > 0:
                color = 'Yellow'
                reason = "部分数据略偏离主流，建议关注"
            else:
                color = 'Green'
                reason = "数据一致，各项正常"

            results.append({
                'original_index': item['original_index'],
                'color': color,
                'reason': reason,
                'details': {
                    'amount': item['amount'],
                    'duration_days': item['duration_days'],
                    'manager_name': item['manager_name'],
                    'cert_number': item['cert_number']
                }
            })

        return {
            "status": "success",
            "message": "扫描完成",
            "results": results
        }
