import datetime
import math
from typing import List, Dict, Any, Tuple

def parse_date(date_val: Any, default_date: datetime.date) -> datetime.date:
    if not date_val:
        return default_date
    if isinstance(date_val, datetime.datetime):
        return date_val.date()
    if isinstance(date_val, datetime.date):
        return date_val
    if isinstance(date_val, str):
        try:
            return datetime.datetime.strptime(date_val, "%Y-%m-%d").date()
        except ValueError:
            pass
    return default_date

def analyze_bid_bond_returns(records: List[Dict[str, Any]], current_date: datetime.date = None) -> Dict[str, Any]:
    if current_date is None:
        current_date = datetime.date.today()

    enriched_records = []
    audit_log = []
    days_held_list = []

    for i, record in enumerate(records):
        record_id = record.get("id", f"auto_id_{i}")

        # Self-healing missing/dirty parameters
        raw_bid_open_date = record.get("bid_open_date")
        bid_open_date = parse_date(raw_bid_open_date, current_date)
        if raw_bid_open_date != bid_open_date:
            audit_log.append(f"Record {record_id}: bid_open_date fallback to {bid_open_date}")

        raw_return_date = record.get("return_date")
        return_date = parse_date(raw_return_date, current_date)
        if raw_return_date != return_date and raw_return_date is not None:
             audit_log.append(f"Record {record_id}: return_date fallback to {return_date}")

        # Self-healing dates inversion
        if return_date < bid_open_date:
            audit_log.append(f"Record {record_id}: return_date {return_date} is before bid_open_date {bid_open_date}. Swapping.")
            bid_open_date, return_date = return_date, bid_open_date

        days_held = (return_date - bid_open_date).days
        days_held_list.append(days_held)

        enriched_record = dict(record)
        enriched_record["id"] = record_id
        enriched_record["bid_open_date"] = bid_open_date
        enriched_record["return_date"] = return_date
        enriched_record["days_held"] = days_held
        enriched_records.append(enriched_record)

    stats = _compute_stats(days_held_list)

    threshold_top_3_idx = max(0, int(len(days_held_list) * 0.97))
    sorted_days = sorted(days_held_list)
    top_3_threshold = sorted_days[threshold_top_3_idx] if sorted_days else 0

    for record in enriched_records:
        days = record["days_held"]

        if days >= top_3_threshold and len(records) >= 33: # Top 3% check requires enough records to make sense
            if stats["iqr_upper_bound"] is not None and days > stats["iqr_upper_bound"]:
                record["mark"] = "RED"
                record["reason"] = f"退还用时（{days}天）严重超出正常范围（正常上限约{stats['iqr_upper_bound']:.1f}天），极大可能是异常滞留，需立即人工干预！"
            elif stats["std_dev"] > 0 and (days - stats["mean"]) / stats["std_dev"] > 2:
                record["mark"] = "RED"
                record["reason"] = f"退还用时（{days}天）远超平均水平（平均约{stats['mean']:.1f}天），存在极大滞留风险，请核实退款进度。"
            else:
                record["mark"] = "YELLOW"
                record["reason"] = f"退还用时（{days}天）处于所有记录的前3%，用时较长，请保持关注。"
        elif days > stats["iqr_upper_bound"] and stats["iqr_upper_bound"] is not None:
             record["mark"] = "RED"
             record["reason"] = f"退还用时（{days}天）严重超出正常范围（正常上限约{stats['iqr_upper_bound']:.1f}天），极大可能是异常滞留，需立即人工干预！"
        elif stats["std_dev"] > 0 and (days - stats["mean"]) / stats["std_dev"] > 2:
             record["mark"] = "RED"
             record["reason"] = f"退还用时（{days}天）远超平均水平（平均约{stats['mean']:.1f}天），存在极大滞留风险，请核实退款进度。"
        else:
            record["mark"] = "GREEN"
            record["reason"] = "正常"

    return {
        "records": enriched_records,
        "statistics": stats,
        "audit_log": audit_log
    }

def _compute_stats(values: List[int]) -> Dict[str, Any]:
    n = len(values)
    if n == 0:
        return {
            "mean": 0, "std_dev": 0, "q1": 0, "q3": 0, "iqr": 0, "iqr_upper_bound": None
        }

    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std_dev = math.sqrt(variance)

    sorted_v = sorted(values)

    def get_percentile(p: float) -> float:
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_v[int(k)]
        d0 = sorted_v[int(f)] * (c - k)
        d1 = sorted_v[int(c)] * (k - f)
        return d0 + d1

    q1 = get_percentile(0.25)
    q3 = get_percentile(0.75)
    iqr = q3 - q1
    iqr_upper_bound = q3 + 1.5 * iqr

    return {
        "mean": mean,
        "std_dev": std_dev,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "iqr_upper_bound": iqr_upper_bound
    }
