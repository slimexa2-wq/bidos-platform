import re
import math
from typing import List, Dict, Any, Optional, Tuple

class ProjectTeamResumeAssemblerAnomalyRadar:
    """
    拟派项目经理与驻厂专员社保缴纳证明、资格证书一键成套汇编核对器
    (统计分布异常值 Z-Score/IQR 后台静默扫描与红黄绿三色极简标记器)
    """

    def __init__(self):
        pass

    def _clean_value(self, val: Any) -> float:
        """
        零配置容错，静默清理脏数据。
        如果解析失败或缺省，返回合理的默认值 0.0。
        """
        if val is None:
            return 0.0

        if isinstance(val, (int, float)):
            if math.isnan(val) or math.isinf(val):
                return 0.0
            return max(0.0, float(val)) # 防止负数

        if isinstance(val, str):
            # 提取数字
            matches = re.findall(r'-?\d+\.?\d*', val)
            if matches:
                try:
                    num = float(matches[0])
                    if math.isnan(num) or math.isinf(num):
                        return 0.0
                    return max(0.0, num)
                except ValueError:
                    return 0.0
            else:
                return 0.0

        return 0.0

    def _calculate_iqr_thresholds(self, values: List[float]) -> Tuple[float, float]:
        """
        计算 IQR 上下限
        """
        if not values:
            return 0.0, float('inf')

        n = len(values)
        if n == 1:
            return values[0], values[0]

        sorted_vals = sorted(values)

        # Calculate Q1 (25th percentile)
        q1_idx = (n - 1) * 0.25
        q1_floor = math.floor(q1_idx)
        q1_ceil = math.ceil(q1_idx)
        if q1_floor == q1_ceil:
            q1 = sorted_vals[int(q1_idx)]
        else:
            q1 = sorted_vals[q1_floor] * (q1_ceil - q1_idx) + sorted_vals[q1_ceil] * (q1_idx - q1_floor)

        # Calculate Q3 (75th percentile)
        q3_idx = (n - 1) * 0.75
        q3_floor = math.floor(q3_idx)
        q3_ceil = math.ceil(q3_idx)
        if q3_floor == q3_ceil:
            q3 = sorted_vals[int(q3_idx)]
        else:
            q3 = sorted_vals[q3_floor] * (q3_ceil - q3_idx) + sorted_vals[q3_ceil] * (q3_idx - q3_floor)

        iqr = q3 - q1

        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        return lower_bound, upper_bound

    def _calculate_z_scores(self, values: List[float]) -> List[float]:
        """
        计算 Z-Score
        """
        n = len(values)
        if n <= 1:
            return [0.0] * n

        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return [0.0] * n

        return [(x - mean) / std_dev for x in values]

    def _determine_anomaly_score(self, value: float, z_score: float, lower_bound: float, upper_bound: float) -> float:
        """
        综合 Z-score 和 IQR 判断异常程度评分，分数越高越异常
        """
        score = 0.0

        # Z-score component
        z_abs = abs(z_score)
        if z_abs > 3:
            score += z_abs * 2
        elif z_abs > 2:
            score += z_abs

        # IQR component
        if value > upper_bound:
            diff = value - upper_bound
            # 避免除以0
            denom = upper_bound if upper_bound > 0 else 1.0
            score += (diff / denom) * 5
        elif value < lower_bound:
            diff = lower_bound - value
            denom = lower_bound if lower_bound > 0 else 1.0
            score += (diff / denom) * 5

        return score

    def scan_anomalies(self, records: List[Dict[str, Any]], target_field: str = "social_security_months") -> List[Dict[str, Any]]:
        """
        扫描异常并进行红黄绿三色标记。
        自动高亮真正需要人工关注的 Top 3% 异常项，其余 97% 正常项静默放行。

        返回原始记录的副本，带有以下新增字段:
        - _anomaly_color: "red", "yellow", 或 "green"
        - _anomaly_reason: 大白话原因 (仅红色和黄色项有)
        - _cleaned_value: 清理后的数值，便于核对
        """
        if not records:
            return []

        # 1. 脏数据自愈：提取和清洗目标字段值
        cleaned_values = []
        result_records = []
        for rec in records:
            new_rec = dict(rec) # 浅拷贝
            # 缺参自动设为合理默认值 (默认 0.0)
            raw_val = rec.get(target_field, None)
            cleaned_val = self._clean_value(raw_val)
            new_rec["_cleaned_value"] = cleaned_val
            cleaned_values.append(cleaned_val)
            result_records.append(new_rec)

        # 2. 统计分布异常值分析 (Z-Score & IQR)
        lower_bound, upper_bound = self._calculate_iqr_thresholds(cleaned_values)
        z_scores = self._calculate_z_scores(cleaned_values)

        # 3. 计算每个记录的综合异常分数
        anomaly_scores = []
        for i, val in enumerate(cleaned_values):
            score = self._determine_anomaly_score(val, z_scores[i], lower_bound, upper_bound)
            anomaly_scores.append((score, i, val, z_scores[i]))

        # 4. 按异常分数降序排列，找出 Top 3%
        # 如果总数很少，至少需要有一定的分数才会被认为是红色
        anomaly_scores.sort(reverse=True, key=lambda x: x[0])

        total_count = len(records)
        top_3_percent_count = max(1, math.ceil(total_count * 0.03)) if total_count >= 10 else 0

        # 极少数情况下(例如只有1个或全是一样的值)，如果没有实际的异常(score=0)，就不标红
        actual_red_count = 0
        for i in range(min(top_3_percent_count, len(anomaly_scores))):
            if anomaly_scores[i][0] > 0:
                actual_red_count += 1

        # 强制：Top 3% 但有明显异常的标红
        # 对于分数极高但不属于 Top 3% 的情况，为了绝对简单，遵循严格的 Top 3% 红线

        for rank, (score, original_idx, val, z) in enumerate(anomaly_scores):
            rec = result_records[original_idx]

            if rank < actual_red_count:
                # 红色：Top 3% 真正需要关注的异常
                rec["_anomaly_color"] = "red"
                if val > upper_bound or z > 2:
                    rec["_anomaly_reason"] = f"该数值 ({val}) 远高于其他项 (平均值周边)，疑似异常夸大或数据录入错误，请人工核验。"
                elif val < lower_bound or z < -2:
                    rec["_anomaly_reason"] = f"该数值 ({val}) 远低于正常范围，疑似资质或社保月份不足，存在废标风险，请重点关注。"
                else:
                    rec["_anomaly_reason"] = f"数值分布位于最极端的 3% (Z-score: {z:.1f})，请人工看一眼。"
            elif score > 0:
                # 黄色：有轻微异常但不属于 Top 3%，静默放行但可作为次要警告
                # 根据铁律，前端其实不需要展示黄色的复杂拦截，但后端保留标记
                rec["_anomaly_color"] = "yellow"
                rec["_anomaly_reason"] = f"稍微偏离正常范围 ({val})，但未触发顶级风险。"
            else:
                # 绿色：97% 正常项静默放行
                rec["_anomaly_color"] = "green"
                rec["_anomaly_reason"] = ""

        return result_records
