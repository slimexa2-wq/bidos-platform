import datetime
import math
import statistics
from typing import List, Dict, Any, Optional

class ExpiryData:
    def __init__(self, id: str, type: str, expiry_date: Optional[str] = None, current_date: Optional[str] = None):
        self.id = id
        self.type = type
        self.expiry_date = expiry_date
        self.current_date = current_date

        self.days_to_expiry = self._calculate_days()
        self.label = 'UNKNOWN'
        self.is_anomaly = False
        self.anomaly_reason = ""

    def _calculate_days(self) -> Optional[int]:
        # Dirty data self-healing and missing parameter default fallbacks
        if not self.expiry_date:
            return None

        try:
            # Self-heal common dirty formats like YYYY/MM/DD or YYYY.MM.DD to YYYY-MM-DD
            clean_date = str(self.expiry_date).replace('/', '-').replace('.', '-')
            exp = datetime.datetime.strptime(clean_date, "%Y-%m-%d").date()

            if self.current_date:
                clean_curr = str(self.current_date).replace('/', '-').replace('.', '-')
                curr = datetime.datetime.strptime(clean_curr, "%Y-%m-%d").date()
            else:
                curr = datetime.date.today()

            return (exp - curr).days
        except (ValueError, TypeError, AttributeError):
            return None

class QualificationExpiryCheckerAnomalyRadar:
    """
    营业执照/劳务派遣证/人力资源许可证/ISO三体系有效期自动核验器
    统计分布异常值(Z-Score/IQR)后台静默扫描与红黄绿三色极简标记器
    """

    def __init__(self, anomaly_method: str = 'iqr', z_score_threshold: float = 2.0, iqr_multiplier: float = 1.5):
        """
        :param anomaly_method: 'iqr' or 'zscore'
        :param z_score_threshold: 异常阈值
        :param iqr_multiplier: IQR 乘数
        """
        self.anomaly_method = anomaly_method
        self.z_score_threshold = z_score_threshold
        self.iqr_multiplier = iqr_multiplier

    def _apply_simple_labels(self, item: ExpiryData) -> None:
        """极简红黄绿标记: < 30红, 30-90黄, > 90绿"""
        if item.days_to_expiry is None:
            item.label = 'RED' # 缺失或无法解析的日期直接标红，需要人工介入
        elif item.days_to_expiry <= 30:
            item.label = 'RED'
        elif item.days_to_expiry <= 90:
            item.label = 'YELLOW'
        else:
            item.label = 'GREEN'

    def _detect_anomalies(self, data: List[ExpiryData]) -> None:
        """后台静默扫描，自动高亮真正需要人工关注的Top 3%异常项"""
        # 仅对正常解析出天数的数据进行统计分析
        valid_data = [d for d in data if d.days_to_expiry is not None]
        if not valid_data:
            return

        days_list = [d.days_to_expiry for d in valid_data]

        lower_bound = -float('inf')

        # 即使数据少，我们仍然可能想标记前 3%（实际上是至少 1 个）为异常，但因为总数太少，可能全是正常数据。
        # 原逻辑：
        if len(days_list) < 4:
            pass
        elif self.anomaly_method == 'iqr':
            q1 = statistics.quantiles(days_list, n=4)[0]
            q3 = statistics.quantiles(days_list, n=4)[-1]
            iqr = q3 - q1
            lower_bound = q1 - self.iqr_multiplier * iqr
            # 上边界不作为异常，因为有效期长是好事
        elif self.anomaly_method == 'zscore':
            mean_days = statistics.mean(days_list)
            stdev_days = statistics.stdev(days_list)
            if stdev_days > 0:
                lower_bound = mean_days - self.z_score_threshold * stdev_days

        # 标记异常
        # Top 3% 异常项：这只是一个概念目标，实际通过统计学（如低于下界）来找出真正的离群值
        # 额外：如果已经过期或极度接近过期（<0），无条件算作异常

        # 收集所有的得分以选取 top 3%
        # 这里我们将“异常程度”定义为：与 90 天（安全期）的差距，越小越异常，或者单纯按天数从小到大排
        # 题目要求高亮Top 3%，可以通过排序强制取前 3%（向上取整）

        # 先按天数排序（升序）
        sorted_valid = sorted(valid_data, key=lambda x: x.days_to_expiry) # type: ignore
        # 只在有明显异常（过期或天数低于极短阈值），才视为异常，而不是单纯标记Top 3%哪怕天数很长
        # “自动高亮真正需要人工关注的Top 3%异常项”
        top_3_count = math.ceil(len(valid_data) * 0.03)

        if top_3_count > 0:
            for i in range(top_3_count):
                item = sorted_valid[i]

                # 如果这个最短的并不短（比如 > 90），就不应该算作异常，因为不需要人工关注
                if item.days_to_expiry > 90 and item.days_to_expiry >= lower_bound:
                    continue

                item.is_anomaly = True

                # 给出大白话原因
                if item.days_to_expiry < 0: # type: ignore
                    item.anomaly_reason = f"已经过期 {abs(item.days_to_expiry)} 天，请立即处理！" # type: ignore
                elif item.days_to_expiry <= 30: # type: ignore
                    item.anomaly_reason = f"距离过期仅剩 {item.days_to_expiry} 天，情况紧急。"
                elif item.days_to_expiry < lower_bound: # type: ignore
                    item.anomaly_reason = f"与其他资质相比，该资质有效期（{item.days_to_expiry}天）显著偏短，存在统计学异常风险。"
                else:
                    item.anomaly_reason = f"在所有资质中排名最靠前（最短的3%），距离过期剩余 {item.days_to_expiry} 天，需重点关注。"

    def process(self, raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        主入口
        :param raw_data: [{'id': '1', 'type': '营业执照', 'expiry_date': '2023-10-01'}]
        """
        if not raw_data:
            return []

        parsed_data = []
        for item in raw_data:
            # 缺参默认兜底
            id = item.get('id', 'unknown_id')
            type = item.get('type', '未知资质')
            expiry_date = item.get('expiry_date')
            current_date = item.get('current_date')

            parsed = ExpiryData(id, type, expiry_date, current_date)
            self._apply_simple_labels(parsed)
            parsed_data.append(parsed)

        self._detect_anomalies(parsed_data)

        # 对于无法解析的日期，也应被强制视为异常（数据脏到无法自愈）
        for item in parsed_data:
            if item.days_to_expiry is None:
                item.is_anomaly = True
                item.anomaly_reason = "日期格式无法识别或缺失，无法计算有效期，请人工核查。"
                item.label = 'RED'

        # 结果组装（零配置容错，极简输出）
        results = []
        for item in parsed_data:
            results.append({
                'id': item.id,
                'type': item.type,
                'days_to_expiry': item.days_to_expiry,
                'label': item.label,
                'is_anomaly': item.is_anomaly,
                'anomaly_reason': item.anomaly_reason if item.is_anomaly else ""
            })

        return results
