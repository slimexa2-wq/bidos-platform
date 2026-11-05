import math
import statistics

def clean_value(val):
    """
    Clean the weight value: handle None, NaN, Infinity, negative values, and invalid types.
    Auto-heals by returning 0.0 for invalid/missing or absolute value for negatives.
    """
    if val is None:
        return 0.0
    try:
        fval = float(val)
        if math.isnan(fval) or math.isinf(fval):
            return 0.0
        return abs(fval)
    except (ValueError, TypeError):
        return 0.0

def generate_proposal_outline(criteria=None, total_pages=100):
    """
    根据招标评分标准分值权重自动分配技术标各章节篇幅与得分点大纲生成器
    包含统计分布异常值(Z-Score/IQR)后台静默扫描与红黄绿三色极简标记
    自动高亮真正需要人工关注的Top 3%异常项并给出大白话原因，其余97%正常项静默放行
    """
    # 缺参自动设合理默认值
    if criteria is None:
        return []

    if not isinstance(criteria, list):
        # 容错：如果传入的不是列表，尝试转换为单项列表
        criteria = [criteria]

    # 默认总页数兜底
    try:
        total_pages = int(total_pages)
        if total_pages <= 0:
            total_pages = 100
    except (ValueError, TypeError):
        total_pages = 100

    cleaned_data = []
    for i, item in enumerate(criteria):
        if not isinstance(item, dict):
            # 容错：直接传入数字或字符串等
            item = {"name": f"未命名章节_{i+1}", "weight": clean_value(item)}
        else:
            item = {
                "name": str(item.get("name", f"未命名章节_{i+1}")),
                "weight": clean_value(item.get("weight", 0))
            }
        cleaned_data.append(item)

    if not cleaned_data:
        return []

    weights = [x["weight"] for x in cleaned_data]
    total_weight = sum(weights)

    n = len(cleaned_data)
    results = []

    # 计算分配页数
    for item in cleaned_data:
        if total_weight > 0:
            pages = round((item["weight"] / total_weight) * total_pages)
        else:
            pages = round(total_pages / n)

        results.append({
            "name": item["name"],
            "weight": item["weight"],
            "pages": pages,
            "anomaly_score": 0.0,
            "marker": "Green",
            "reason": "正常项，静默放行"
        })

    if n < 3:
        # 数据量过少不足以进行异常检测，直接静默放行
        return results

    mean_w = statistics.mean(weights)
    stdev_w = statistics.pstdev(weights)

    sorted_w = sorted(weights)
    q1 = sorted_w[n // 4]
    q3 = sorted_w[(3 * n) // 4]
    iqr = q3 - q1

    # 后台静默扫描统计分布异常值 (Z-Score/IQR)
    for item in results:
        w = item["weight"]

        # Z-Score
        z_score = abs(w - mean_w) / stdev_w if stdev_w > 0 else 0.0

        # IQR Outlier Score
        iqr_outlier = 0.0
        if iqr > 0:
            if w > q3 + 1.5 * iqr:
                iqr_outlier = (w - (q3 + 1.5 * iqr)) / iqr
            elif w < q1 - 1.5 * iqr:
                iqr_outlier = ((q1 - 1.5 * iqr) - w) / iqr

        # 综合异常得分，取较高者
        item["anomaly_score"] = max(z_score, iqr_outlier)

    # 高亮 Top 3% 异常项（至少 1 项如果有异常）
    top_3_pct_count = max(1, math.ceil(n * 0.03))

    if stdev_w > 0:
        # 按异常程度倒序
        sorted_indices = sorted(range(n), key=lambda i: results[i]["anomaly_score"], reverse=True)

        # 标记 Top 3% 为 Red
        for idx in sorted_indices[:top_3_pct_count]:
            item = results[idx]
            if item["anomaly_score"] > 0:
                item["marker"] = "Red"
                if item["weight"] > mean_w:
                    item["reason"] = f"大白话提示：这部分权重({item['weight']})比平均水平({mean_w:.1f})高出不少，分配了{item['pages']}页，建议您人工看一眼别写得太啰嗦。"
                else:
                    item["reason"] = f"大白话提示：这部分权重({item['weight']})比平均水平({mean_w:.1f})低了不少，只分配了{item['pages']}页，建议您确认下关键得分点有没有漏掉。"

        # 标记 Top 10% 里的剩余项为 Yellow
        top_10_pct_count = max(1, math.ceil(n * 0.10))
        for idx in sorted_indices[top_3_pct_count:top_10_pct_count]:
            item = results[idx]
            if item["anomaly_score"] > 1.0:
                item["marker"] = "Yellow"
                item["reason"] = "轻微偏离均值，系统已自动搞定，静默放行。"

    return results
