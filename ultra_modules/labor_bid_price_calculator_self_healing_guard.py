import time
import copy
import uuid
import re

def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        if isinstance(val, str):
            val = val.replace(',', '').replace('%', '')
            val = re.sub(r'[^\d\.\-]', '', val) # remove currency symbols, spaces, etc
            if not val:
                return default
        return float(val)
    except (ValueError, TypeError):
        return default

class LaborBidPriceSelfHealingGuard:
    def __init__(self):
        self._snapshots = {}

    def _clean_value(self, val):
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            # Handle common dirty data: remove currency symbols, letters, spaces
            # Keep digits, dot, comma, percent sign, minus sign
            cleaned = re.sub(r'[^\d\.\,\%\-]', '', val)
            if not cleaned:
                return 0.0
            return cleaned
        return 0.0

    def calculate_labor_bid(self, params):
        """
        Core logic to calculate labor bid price based on inputs.
        """
        if params is None:
            params = {}

        base_salary = safe_float(params.get('base_salary'), 0.0)
        bonus = safe_float(params.get('bonus'), 0.0)
        allowance = safe_float(params.get('allowance'), 0.0)

        total_salary = base_salary + bonus + allowance

        # Social Security Compliance
        social_security_base = safe_float(params.get('social_security_base'), total_salary)
        min_social_security_base = safe_float(params.get('min_social_security_base'), 0.0)
        actual_ss_base = max(social_security_base, min_social_security_base)
        social_security_rate = safe_float(params.get('social_security_rate'), 0.0) / 100.0
        social_security_cost = actual_ss_base * social_security_rate

        # Provident Fund Compliance
        provident_fund_base = safe_float(params.get('provident_fund_base'), total_salary)
        min_provident_fund_base = safe_float(params.get('min_provident_fund_base'), 0.0)
        actual_pf_base = max(provident_fund_base, min_provident_fund_base)
        provident_fund_rate = safe_float(params.get('provident_fund_rate'), 0.0) / 100.0
        provident_fund_cost = actual_pf_base * provident_fund_rate

        # Disabled Fund
        disabled_fund_base = safe_float(params.get('disabled_fund_base'), total_salary)
        disabled_fund_rate = safe_float(params.get('disabled_fund_rate'), 0.0) / 100.0
        disabled_fund_cost = disabled_fund_base * disabled_fund_rate

        # Base costs
        management_fee = safe_float(params.get('management_fee'), 0.0)
        total_cost_before_tax = total_salary + social_security_cost + provident_fund_cost + disabled_fund_cost + management_fee

        tax_rate = safe_float(params.get('tax_rate'), 0.0) / 100.0

        provided_bid_price = params.get('provided_bid_price')
        if provided_bid_price is not None:
            provided_bid_price = safe_float(provided_bid_price, 0.0)

            tax = provided_bid_price * tax_rate
            total_cost = total_cost_before_tax + tax
            margin = provided_bid_price - total_cost

            if provided_bid_price == 0:
                margin_rate = 0.0
            else:
                margin_rate = margin / provided_bid_price

            calculated_bid_price = provided_bid_price
        else:
            target_margin_rate = safe_float(params.get('target_margin_rate'), 0.0) / 100.0

            denominator = 1.0 - tax_rate - target_margin_rate
            if denominator <= 0:
                calculated_bid_price = total_cost_before_tax
            else:
                calculated_bid_price = total_cost_before_tax / denominator

            tax = calculated_bid_price * tax_rate
            margin = calculated_bid_price * target_margin_rate
            margin_rate = target_margin_rate
            total_cost = total_cost_before_tax + tax

        return {
            'total_salary': round(total_salary, 2),
            'actual_ss_base': round(actual_ss_base, 2),
            'social_security_cost': round(social_security_cost, 2),
            'actual_pf_base': round(actual_pf_base, 2),
            'provident_fund_cost': round(provident_fund_cost, 2),
            'disabled_fund_cost': round(disabled_fund_cost, 2),
            'total_cost_before_tax': round(total_cost_before_tax, 2),
            'tax': round(tax, 2),
            'total_cost': round(total_cost, 2),
            'margin': round(margin, 2),
            'margin_rate': round(margin_rate, 4),
            'calculated_bid_price': round(calculated_bid_price, 2)
        }

    def calculate_with_guard(self, params):
        """
        Calculates labor bid price with automatic self-healing of dirty data.
        Returns a snapshot_id for a 5-second Undo window.
        """
        snapshot_id = str(uuid.uuid4())
        timestamp = time.time()

        original_params = copy.deepcopy(params)

        # Self-healing: handle extreme boundary where params is not a dict
        if not isinstance(params, dict):
            params = {}

        healed_params = {}
        for k, v in params.items():
            healed_params[k] = self._clean_value(v)

        try:
            result = self.calculate_labor_bid(healed_params)
        except Exception:
            # Ultimate silent fallback if core engine crashes
            result = self.calculate_labor_bid({})

        self._snapshots[snapshot_id] = {
            'timestamp': timestamp,
            'params': original_params,
            'result': result
        }

        # Clean up old snapshots silently
        self._cleanup_snapshots()

        return {
            'snapshot_id': snapshot_id,
            'result': result,
            'healed_params': healed_params
        }

    def undo(self, snapshot_id, current_time=None):
        """
        Undo operation using snapshot_id. Valid for 5 seconds.
        """
        if current_time is None:
            current_time = time.time()

        if snapshot_id not in self._snapshots:
            return {'success': False, 'error': 'Snapshot not found'}

        snapshot = self._snapshots[snapshot_id]
        if current_time - snapshot['timestamp'] > 5.0:
            del self._snapshots[snapshot_id]
            return {'success': False, 'error': 'Undo window expired'}

        restored_params = snapshot['params']
        # Once undone, consume the snapshot
        del self._snapshots[snapshot_id]

        return {
            'success': True,
            'restored_params': restored_params
        }

    def _cleanup_snapshots(self):
        current_time = time.time()
        expired = [k for k, v in self._snapshots.items() if current_time - v['timestamp'] > 5.0]
        for k in expired:
            del self._snapshots[k]
