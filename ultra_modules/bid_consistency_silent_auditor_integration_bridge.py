import re

class BidConsistencySilentAuditorBridge:
    def __init__(self):
        self.default_values = {
            "amount": 0.0,
            "amount_cn": "零元整",
            "duration_days": 0,
            "manager_name": "未指定项目经理",
            "manager_cert": "未指定证号"
        }

    def _extract_float(self, val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).strip()
        match = re.search(r"(\d+(\.\d+)?)", val)
        if match:
            return float(match.group(1))
        return None

    def _extract_int(self, val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)
        val = str(val).strip()
        match = re.search(r"(\d+)", val)
        if match:
            return int(match.group(1))
        return None

    def map_schema(self, data):
        if not isinstance(data, dict):
            return {}

        mapped = {}

        # 1. Amount
        raw_amt = data.get("amount")
        if raw_amt is None: raw_amt = data.get("bid_amount")
        if raw_amt is None: raw_amt = data.get("tenderAmount")
        if raw_amt is None: raw_amt = data.get("报价")
        if raw_amt is None: raw_amt = data.get("old_amount")
        amt = self._extract_float(raw_amt)
        if amt is not None:
            mapped["amount"] = amt

        # 2. Amount Chinese
        raw_amt_cn = data.get("amount_cn")
        if raw_amt_cn is None: raw_amt_cn = data.get("bid_amount_cn")
        if raw_amt_cn is None: raw_amt_cn = data.get("tenderAmountCN")
        if raw_amt_cn is None: raw_amt_cn = data.get("报价大写")
        if raw_amt_cn is not None and str(raw_amt_cn).strip():
            mapped["amount_cn"] = str(raw_amt_cn).strip()

        # 3. Duration Days
        raw_days = data.get("duration_days")
        if raw_days is None: raw_days = data.get("duration")
        if raw_days is None: raw_days = data.get("period")
        if raw_days is None: raw_days = data.get("工期")
        days = self._extract_int(raw_days)
        if days is not None:
            mapped["duration_days"] = days

        # 4. Manager Name
        raw_mgr = data.get("manager_name")
        if raw_mgr is None: raw_mgr = data.get("pm_name")
        if raw_mgr is None: raw_mgr = data.get("projectManager")
        if raw_mgr is None: raw_mgr = data.get("项目经理")
        if raw_mgr is not None and str(raw_mgr).strip():
            mapped["manager_name"] = str(raw_mgr).strip()

        # 5. Manager Cert
        raw_cert = data.get("manager_cert")
        if raw_cert is None: raw_cert = data.get("pm_cert")
        if raw_cert is None: raw_cert = data.get("cert_no")
        if raw_cert is None: raw_cert = data.get("执业证号")
        if raw_cert is not None and str(raw_cert).strip():
            mapped["manager_cert"] = str(raw_cert).strip()

        return mapped

    def process(self, payload):
        if not isinstance(payload, dict):
            payload = {}

        main_raw = payload.get("main_data", {})
        if not isinstance(main_raw, dict):
            main_raw = {}

        chapters_raw = payload.get("chapters", [])
        if not isinstance(chapters_raw, list):
            chapters_raw = []

        main_mapped = self.map_schema(main_raw)
        chapters_mapped = [self.map_schema(ch) for ch in chapters_raw]

        # Determine truth
        truth = {}
        for k in self.default_values:
            if k in main_mapped:
                truth[k] = main_mapped[k]
            else:
                for ch in chapters_mapped:
                    if k in ch:
                        truth[k] = ch[k]
                        break

        # Apply defaults
        for k, v in self.default_values.items():
            if k not in truth:
                truth[k] = v

        # Re-build output enforcing consistency
        out_main = dict(main_raw)
        for k, v in truth.items():
            out_main[k] = v

        out_chapters = []
        for ch_raw in chapters_raw:
            if not isinstance(ch_raw, dict):
                ch_raw = {}
            new_ch = dict(ch_raw)
            for k, v in truth.items():
                new_ch[k] = v
            out_chapters.append(new_ch)

        return {
            "main_data": out_main,
            "chapters": out_chapters
        }
