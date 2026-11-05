import math
import statistics
from typing import List, Dict, Any, Optional

class SealSignatureAnomalyRadar:
    """
    千页标书全文需盖公章/法人章/骑缝章/签字页码一键精准定位清单生成器
    (统计分布异常值后台静默扫描与红黄绿三色极简标记器).
    """

    def __init__(self, fallback_score: float = 0.0):
        # 缺参自动设合理默认值，零配置容错
        self.fallback_score = fallback_score

    def _clean_data(self, data: List[Any]) -> List[Dict[str, float]]:
        """
        后台静默清洗脏数据。
        如果条目是字典但缺少必需字段，则补齐。
        如果是非正常格式(如 None, 字符串等)，尝试转换，失败则忽略或设为默认值。
        """
        cleaned = []
        if not data:
            return cleaned

        for i, item in enumerate(data):
            if isinstance(item, dict):
                page_idx = item.get("page_index")
                score = item.get("score")

                # Fix page_index
                if page_index_valid(page_idx):
                    real_page = int(page_idx)
                else:
                    real_page = i

                # Fix score
                if score_valid(score):
                    real_score = float(score)
                else:
                    real_score = self.fallback_score

                cleaned.append({"page_index": real_page, "score": real_score})
            elif isinstance(item, (int, float)):
                # 如果只给了数字，当作 score，page_index 就是索引
                cleaned.append({"page_index": i, "score": float(item)})
            elif isinstance(item, tuple) or isinstance(item, list):
                # 假设格式为 (page_index, score) 或只给 score
                if len(item) >= 2:
                    page_idx = item[0]
                    score = item[1]
                    if page_index_valid(page_idx):
                        real_page = int(page_idx)
                    else:
                        real_page = i
                    if score_valid(score):
                        real_score = float(score)
                    else:
                        real_score = self.fallback_score
                    cleaned.append({"page_index": real_page, "score": real_score})
                elif len(item) == 1:
                    score = item[0]
                    if score_valid(score):
                        real_score = float(score)
                    else:
                        real_score = self.fallback_score
                    cleaned.append({"page_index": i, "score": real_score})
                else:
                    # 空列表/元组
                    cleaned.append({"page_index": i, "score": self.fallback_score})
            else:
                # 无法识别的脏数据，静默赋默认值
                cleaned.append({"page_index": i, "score": self.fallback_score})

        return cleaned

    def _compute_z_scores(self, cleaned_data: List[Dict[str, float]]) -> List[float]:
        """
        计算 Z-Score
        """
        if not cleaned_data:
            return []

        scores = [item["score"] for item in cleaned_data]
        if len(scores) < 2:
            return [0.0] * len(scores)

        mean = statistics.mean(scores)
        try:
            stdev = statistics.stdev(scores)
        except statistics.StatisticsError:
            stdev = 0.0

        if stdev == 0.0:
            return [0.0] * len(scores)

        z_scores = [(s - mean) / stdev for s in scores]
        return z_scores

    def scan_and_locate(self, pages_data: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """
        主入口：一键生成定位清单。
        自动高亮真正需要人工关注的 Top 3% 异常项，其余 97% 静默放行。
        返回的数据包含 color (red, yellow, green) 和 reason (大白话原因)。
        """
        # 缺参自动设合理默认值
        if pages_data is None:
            pages_data = []

        cleaned = self._clean_data(pages_data)
        if not cleaned:
            return []

        n = len(cleaned)
        z_scores = self._compute_z_scores(cleaned)

        # 组装带 Z-Score 的完整数据
        for i in range(n):
            cleaned[i]["z_score"] = z_scores[i]

        # 排序以确定 Top 3%
        # 我们的异常通常是"分值特别高"(表示有印章/签字特征)
        # 所以按照 score 降序排列 (如果是正态分布，z_score 降序一样)
        sorted_by_score = sorted(cleaned, key=lambda x: x["score"], reverse=True)

        top_3_count = max(1, math.ceil(n * 0.03)) if n > 0 else 0

        # 记录每页的颜色和原因
        # 考虑到可能所有页面的 score 都一样 (stdev=0)，如果全一样，则全放行 (全 green)
        # 我们用 z_score > 0 作为有意义的异常的辅助条件，如果 z_score 都是 0，说明没有任何页突出

        results = []
        for i, item in enumerate(cleaned):
            page_index = item["page_index"]
            score = item["score"]
            z_score = item["z_score"]

            # 找到在 sorted 里的排名
            rank = 0
            for idx, s_item in enumerate(sorted_by_score):
                if s_item["page_index"] == page_index:
                    rank = idx
                    break

            if z_score > 0 and rank < top_3_count:
                # Top 3% 异常
                color = "red"
                reason = "发现强烈的印章或签字特征，可能是重点盖章页，请人工确认。"
            elif z_score > 1.0:
                # 即使没进 Top 3%，但偏离平均值一个标准差以上，给黄灯提醒
                color = "yellow"
                reason = "存在一定的特征印记，建议顺带瞥一眼。"
            else:
                color = "green"
                reason = "无明显异常特征，已静默放行。"

            results.append({
                "page_index": page_index,
                "score": score,
                "z_score": z_score,
                "color": color,
                "reason": reason
            })

        # 按照 page_index 排序返回，符合人类阅读习惯
        results = sorted(results, key=lambda x: x["page_index"])
        return results

def page_index_valid(val: Any) -> bool:
    if val is None:
        return False
    try:
        int(val)
        return True
    except (ValueError, TypeError):
        return False

def score_valid(val: Any) -> bool:
    if val is None:
        return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False
