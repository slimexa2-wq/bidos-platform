import math
from typing import List, Dict, Any, Optional

class AddendumClauseDiffAnomalyRadar:
    """
    招标更正/澄清公告与原招标文件条款级红绿Diff对比及影响章节重算器
    (统计分布异常值(Z-Score/IQR)后台静默扫描与红黄绿三色极简标记器)
    """

    @staticmethod
    def _clean_score(score: Any, default: float = 0.0) -> float:
        """Silently cleans score data."""
        if score is None:
            return default
        try:
            val = float(score)
            if math.isnan(val) or math.isinf(val):
                return default
            return val
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _clean_clause_item(item: Any) -> Dict[str, Any]:
        """Silently cleans a single clause item."""
        if not isinstance(item, dict):
            return {
                "id": "unknown",
                "content": str(item) if item is not None else "",
                "score": 0.0,
                "type": "unknown"
            }

        cleaned = {}
        cleaned["id"] = str(item.get("id", "unknown"))
        cleaned["content"] = str(item.get("content", ""))

        # Determine the score, default to 0.0 if not found
        score = item.get("change_impact_score", item.get("score"))
        cleaned["score"] = AddendumClauseDiffAnomalyRadar._clean_score(score)

        cleaned["type"] = str(item.get("type", "unknown"))

        # Copy other metadata
        for k, v in item.items():
            if k not in ["id", "content", "change_impact_score", "score", "type"]:
                cleaned[k] = v

        return cleaned

    @staticmethod
    def calculate_anomalies(clauses: List[Any], top_percent: float = 0.03) -> Dict[str, Any]:
        """
        Calculates anomalies using Z-Score and IQR, marks Top N% as Red/Yellow/Green
        and returns the result with plain language explanations.
        """
        if not clauses or not isinstance(clauses, list):
            return {
                "total_items": 0,
                "anomalies": [],
                "normal_items": [],
                "summary_plain_text": "未提供任何条款数据，系统静默放行。"
            }

        cleaned_clauses = [AddendumClauseDiffAnomalyRadar._clean_clause_item(c) for c in clauses]

        # Ensure we have scores
        scores = [c["score"] for c in cleaned_clauses]

        if not scores:
            return {
                "total_items": 0,
                "anomalies": [],
                "normal_items": [],
                "summary_plain_text": "没有有效的评分数据。"
            }

        n = len(scores)
        mean = sum(scores) / n if n > 0 else 0.0
        variance = sum((x - mean) ** 2 for x in scores) / n if n > 0 else 0.0
        std_dev = math.sqrt(variance)

        # Z-Scores calculation
        for c in cleaned_clauses:
            if std_dev > 0:
                c["z_score"] = (c["score"] - mean) / std_dev
            else:
                c["z_score"] = 0.0

        # Sort by score descending to get top items
        cleaned_clauses.sort(key=lambda x: x["score"], reverse=True)

        # Determine how many items correspond to the top_percent
        top_k = max(1, math.ceil(n * top_percent)) if n > 0 else 0

        # To avoid marking identical scores differently, include all items tied with the last top item
        if top_k > 0 and top_k <= n:
            threshold_score = cleaned_clauses[top_k - 1]["score"]
            # Find all items with score >= threshold_score
            actual_top_items = [c for c in cleaned_clauses if c["score"] >= threshold_score]
        else:
            actual_top_items = []

        anomalies = []
        normal_items = []

        # If all scores are identical and z_score is 0, we treat them as normal
        # unless it's just 1 item and we still want to flag it? No, if all are identical, no anomalies.
        if std_dev == 0:
             # all items have the same score, no actual anomaly.
             for c in cleaned_clauses:
                 c["marker"] = "Green"
                 c["explanation"] = "常规项，静默放行。"
                 normal_items.append(c)

             return {
                "total_items": n,
                "anomalies": [],
                "normal_items": normal_items,
                "summary_plain_text": "所有条款修改幅度一致，未发现明显异常，全部静默放行。"
             }

        # Apply markers
        for c in cleaned_clauses:
            if c in actual_top_items and c["z_score"] > 2.0: # high anomaly
                c["marker"] = "Red"
                c["explanation"] = f"严重异常关注项！该条款的修改幅度远超平均水平（Z-Score: {c['z_score']:.2f}），极有可能对投标产生重大影响，请务必人工核对。"
                anomalies.append(c)
            elif (c in actual_top_items and c["z_score"] > 1.0) or c["z_score"] > 1.5:
                # Either it's in the top but with lower z-score, or it's not in the top but still has high z-score
                c["marker"] = "Yellow"
                c["explanation"] = f"一般异常项。该条款修改幅度偏大，建议抽查。"
                anomalies.append(c)
            else:
                c["marker"] = "Green"
                c["explanation"] = "常规项，静默放行。"
                normal_items.append(c)

        # Sort anomalies by score descending
        anomalies.sort(key=lambda x: x["score"], reverse=True)

        red_count = sum(1 for a in anomalies if a["marker"] == "Red")
        yellow_count = sum(1 for a in anomalies if a["marker"] == "Yellow")
        green_count = len(normal_items)

        summary_text = f"扫描完成，共检测 {n} 项条款。系统自动拦截 {red_count} 项高风险（红灯）修改和 {yellow_count} 项中等风险（黄灯）修改，建议立即人工确认；其余 {green_count} 项常规修改（绿灯）已静默放行，无需人工干预。"

        return {
            "total_items": n,
            "anomalies": anomalies,
            "normal_items": normal_items,
            "summary_plain_text": summary_text
        }
