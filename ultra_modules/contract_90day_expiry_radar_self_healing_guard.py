import time
import datetime
import re
import copy

class ContractExpiryRadarSelfHealingGuard:
    def __init__(self, undo_window_seconds=5.0):
        self._data_store = {}
        self._snapshots = []
        self._undo_window_seconds = undo_window_seconds

    def _parse_date(self, date_val):
        if not date_val:
            return None
        if isinstance(date_val, datetime.date):
            if isinstance(date_val, datetime.datetime):
                return date_val.date()
            return date_val
        if isinstance(date_val, str):
            date_str = re.sub(r'[^\d]', '-', date_val.strip())
            parts = [p for p in date_str.split('-') if p]
            if len(parts) >= 3:
                try:
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    if year < 100:
                        year += 2000
                    return datetime.date(year, month, day)
                except Exception:
                    pass
        return None

    def _parse_number(self, val, default=0.0):
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                cleaned = re.sub(r'[^\d.-]', '', val)
                if cleaned:
                    return float(cleaned)
            except Exception:
                pass
        return default

    def _parse_boolean(self, val, default=False):
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            val_lower = val.lower().strip()
            if val_lower in ('true', 'yes', '1', 'y', 't', '已退', '是', 'ok', 'done'):
                return True
            if val_lower in ('false', 'no', '0', 'n', 'f', '未退', '否', 'none'):
                return False
        if isinstance(val, (int, float)):
            return bool(val)
        return default

    def _save_snapshot(self):
        current_time = time.time()
        self._snapshots.append((current_time, copy.deepcopy(self._data_store)))
        # cleanup old snapshots silently
        self._snapshots = [s for s in self._snapshots if current_time - s[0] <= self._undo_window_seconds + 1.0]

    def update_contract(self, contract_id, contract_data):
        """
        Updates or adds a contract. Silently heals dirty data.
        Saves snapshot for undo.
        """
        try:
            self._save_snapshot()

            if contract_id is None:
                return False

            if not isinstance(contract_data, dict):
                contract_data = {}

            # Silent self healing
            end_date = self._parse_date(contract_data.get('end_date'))
            bond_amount = self._parse_number(contract_data.get('bond_amount', 0.0))
            bond_refunded_amount = self._parse_number(contract_data.get('bond_refunded_amount', 0.0))
            is_bond_refunded = self._parse_boolean(contract_data.get('is_bond_refunded', False))

            if bond_amount > 0 and bond_refunded_amount >= bond_amount:
                is_bond_refunded = True

            self._data_store[contract_id] = {
                'end_date': end_date,
                'bond_amount': bond_amount,
                'bond_refunded_amount': bond_refunded_amount,
                'is_bond_refunded': is_bond_refunded,
                'original_data': contract_data
            }
            return True
        except Exception:
            return False

    def undo(self):
        """
        Restores the state to the latest snapshot within 5 seconds window.
        """
        try:
            current_time = time.time()
            valid_snapshots = [s for s in self._snapshots if current_time - s[0] <= self._undo_window_seconds]
            if not valid_snapshots:
                return False

            latest_snapshot = valid_snapshots[-1]
            self._data_store = copy.deepcopy(latest_snapshot[1])

            self._snapshots = [s for s in self._snapshots if s[0] < latest_snapshot[0]]
            return True
        except Exception:
            return False

    def generate_warning_board(self, current_date=None):
        """
        Generates the warning board.
        """
        try:
            if current_date is None:
                current_date = datetime.date.today()
            elif isinstance(current_date, datetime.datetime):
                current_date = current_date.date()
            elif isinstance(current_date, str):
                current_date = self._parse_date(current_date) or datetime.date.today()

            warnings = {
                '90_days': [],
                '60_days': [],
                '30_days': [],
                'expired': [],
                'unreturned_bonds': []
            }

            for cid, data in self._data_store.items():
                end_date = data['end_date']
                bond_amount = data['bond_amount']
                is_bond_refunded = data['is_bond_refunded']

                if end_date:
                    days_remaining = (end_date - current_date).days
                    if days_remaining < 0:
                        warnings['expired'].append(cid)
                    elif days_remaining <= 30:
                        warnings['30_days'].append(cid)
                    elif days_remaining <= 60:
                        warnings['60_days'].append(cid)
                    elif days_remaining <= 90:
                        warnings['90_days'].append(cid)

                if bond_amount > 0 and not is_bond_refunded:
                    warnings['unreturned_bonds'].append(cid)

            return warnings
        except Exception:
            return {
                '90_days': [],
                '60_days': [],
                '30_days': [],
                'expired': [],
                'unreturned_bonds': []
            }
