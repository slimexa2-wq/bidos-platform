import math

def clean_data(val, default_val=0.0):
    if val is None:
        return default_val
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace(',', '').replace(' ', '').replace('¥', '').replace('￥', '')
        try:
            return float(cleaned)
        except ValueError:
            return default_val
    return default_val

def _calculate_iqr_thresholds(values):
    if not values:
        return 0, 0
    sorted_values = sorted(values)
    n = len(sorted_values)

    def get_percentile(data, p):
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return data[int(k)]
        d0 = data[int(f)] * (c - k)
        d1 = data[int(c)] * (k - f)
        return d0 + d1

    q1 = get_percentile(sorted_values, 0.25)
    q3 = get_percentile(sorted_values, 0.75)
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    return lower_bound, upper_bound

def detect_anomalies(bids: list[dict]) -> dict:
    if not bids:
        return {"anomalies": [], "normal": []}

    cleaned_bids = []
    for idx, bid in enumerate(bids):
        cleaned_bid = {
            "id": bid.get("id", f"bid_{idx}"),
            "base_salary": clean_data(bid.get("base_salary"), 3000.0),
            "social_security": clean_data(bid.get("social_security"), 1000.0),
            "provident_fund": clean_data(bid.get("provident_fund"), 300.0),
            "disabled_security_fund": clean_data(bid.get("disabled_security_fund"), 50.0),
            "margin": clean_data(bid.get("margin"), 500.0),
            "tax": clean_data(bid.get("tax"), 200.0),
            "_original": bid
        }
        cleaned_bid["total_price"] = sum([
            cleaned_bid["base_salary"],
            cleaned_bid["social_security"],
            cleaned_bid["provident_fund"],
            cleaned_bid["disabled_security_fund"],
            cleaned_bid["margin"],
            cleaned_bid["tax"]
        ])

        # Check if we did any silent correction on critical fields
        has_dirty_data_healed = False
        for k in ["base_salary", "social_security", "provident_fund", "disabled_security_fund", "margin", "tax"]:
            if bid.get(k) is None or isinstance(bid.get(k), str):
                has_dirty_data_healed = True

        cleaned_bid["_healed"] = has_dirty_data_healed
        cleaned_bids.append(cleaned_bid)

    if len(cleaned_bids) < 3:
        for bid in cleaned_bids:
            bid["color_marker"] = "green"
            bid["reason"] = "正常放行"
        return {"anomalies": [], "normal": cleaned_bids}

    total_prices = [b["total_price"] for b in cleaned_bids]
    sorted_prices = sorted(total_prices)
    n_bids = len(cleaned_bids)

    if n_bids % 2 == 0:
        median_price = (sorted_prices[n_bids//2 - 1] + sorted_prices[n_bids//2]) / 2
    else:
        median_price = sorted_prices[n_bids//2]

    deviations = []
    for b in cleaned_bids:
        dev = abs(b["total_price"] - median_price)
        deviations.append((dev, b))

    deviations.sort(key=lambda x: x[0], reverse=True)
    top_3_count = max(1, math.ceil(n_bids * 0.03))

    lower_bound, upper_bound = _calculate_iqr_thresholds(total_prices)

    anomalies = []
    normal = []

    anomaly_ids = set()
    potential_anomalies = []
    for b in cleaned_bids:
        if b["total_price"] < lower_bound or b["total_price"] > upper_bound:
            potential_anomalies.append(b)

    potential_anomalies.sort(key=lambda x: abs(x["total_price"] - median_price), reverse=True)
    actual_anomalies = potential_anomalies[:top_3_count]

    for b in actual_anomalies:
        anomaly_ids.add(b["id"])

    for bid in cleaned_bids:
        if bid["id"] in anomaly_ids:
            bid["color_marker"] = "red"
            if bid["total_price"] < lower_bound:
                bid["reason"] = "总价过低，存在严重亏损或未交齐社保风险"
            elif bid["total_price"] > upper_bound:
                bid["reason"] = "总价过高，偏离市场正常报价区间"
            else:
                bid["reason"] = "报价结构异常"
            anomalies.append(bid)
        else:
            if bid.get("_healed"):
                bid["color_marker"] = "yellow"
                bid["reason"] = "数据自愈放行(含缺省值或格式修正)"
                normal.append(bid)
            else:
                bid["color_marker"] = "green"
                bid["reason"] = "正常放行"
                normal.append(bid)

    return {
        "anomalies": anomalies,
        "normal": normal
    }
