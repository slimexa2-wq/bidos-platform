import copy
from typing import List, Dict, Any

def scan_anomalies(contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Scans a list of contracts/projects for anomalies related to expiry and bid bond returns.
    Uses robust scoring based on days_to_expiry, bond_delay_days, and bond_amount.
    Self-heals dirty data (e.g., missing values, wrong types).
    Flags the top 3% (roughly) most risky anomalies as 'RED' or 'YELLOW',
    and leaves the rest (97%) as 'GREEN'.
    """
    if not contracts:
        return []

    processed = []

    # Pre-process and calculate initial risk scores for each contract
    for contract in contracts:
        item = copy.deepcopy(contract)

        # Self-healing and parsing dirty data
        # days_to_expiry (lower is worse, negative means expired)
        dte_raw = item.get('days_to_expiry')
        try:
            dte = float(dte_raw) if dte_raw is not None else 999.0
        except (ValueError, TypeError):
            dte = 999.0

        # bond_delay_days (higher is worse)
        bdd_raw = item.get('bond_delay_days')
        try:
            bdd = float(bdd_raw) if bdd_raw is not None else 0.0
        except (ValueError, TypeError):
            bdd = 0.0

        # bond_amount (higher amount means higher priority/risk if delayed)
        ba_raw = item.get('bond_amount')
        try:
            ba = float(ba_raw) if ba_raw is not None else 0.0
        except (ValueError, TypeError):
            ba = 0.0

        item['_dte'] = dte
        item['_bdd'] = bdd
        item['_ba'] = ba

        # Base risk calculation
        # Maximize risk for: low days_to_expiry, high bond_delay_days, high bond_amount
        # Heuristic risk function:
        # Expiry risk: if dte <= 90, risk increases. If dte < 0, massive risk.
        expiry_risk = max(0, 90 - dte) * 2

        # Bond delay risk: delay days * (1 + log10(bond_amount) scaling)
        bond_risk = bdd * (1 + (ba / 10000.0))

        raw_score = expiry_risk + bond_risk
        item['_raw_score'] = raw_score

        processed.append(item)

    # Statistical Anomaly Detection (IQR or Z-Score approach)
    # Since we need exactly top 3% (or nearest integer representing 3%) to be flagged,
    # and the rest 97% to be GREEN.
    n = len(processed)

    if n > 0:
        # Sort by raw score descending (highest risk first)
        processed.sort(key=lambda x: x['_raw_score'], reverse=True)

        # Determine the cutoff index for top 3%
        # At least 1 item if n >= 34, else we might not flag any if we strictly do floor(n*0.03)
        # To be safe and meet "Top 3%" requirement, we can use round(n * 0.03) or ceil(n * 0.03)
        import math
        top_k = max(1, math.ceil(n * 0.03)) if n >= 10 else (1 if processed[0]['_raw_score'] > 0 else 0)
        # Actually, let's strictly follow "Top 3%", but handle small lists gracefully.
        top_k = math.ceil(n * 0.03)
        if top_k == 0 and n > 0 and processed[0]['_raw_score'] > 0:
            top_k = 1 # Always flag at least 1 if there's any risk in very small batches?
            # Requirements say "Top 3%". If n=10, 3% is 0.3. Ceil is 1.

        for i, item in enumerate(processed):
            if i < top_k and item['_raw_score'] > 0:
                # Top k anomalies
                if top_k == 1:
                    item['status_color'] = 'RED'
                else:
                    if i < (top_k / 2):
                        item['status_color'] = 'RED'
                    else:
                        item['status_color'] = 'YELLOW'

                reason = []
                if item['_dte'] <= 90:
                    if item['status_color'] == 'RED':
                        reason.append(f"合同距离到期仅剩{item['_dte']}天，需紧急关注")
                    else:
                        reason.append(f"合同距离到期仅剩{item['_dte']}天")
                if item['_bdd'] > 0:
                    if item['status_color'] == 'RED':
                        reason.append(f"履约保证金逾期{item['_bdd']}天未退回（金额：{item['_ba']}元）")
                    else:
                        reason.append(f"保证金逾期{item['_bdd']}天未退")
                if not reason:
                    reason.append("综合异常评分较高，请人工核查")
                item['anomaly_reason'] = "；".join(reason)
            else:
                item['status_color'] = 'GREEN'
                item['anomaly_reason'] = '未发现异常，静默放行'

            # Clean up internal fields
            item.pop('_dte', None)
            item.pop('_bdd', None)
            item.pop('_ba', None)
            item.pop('_raw_score', None)

    return processed
