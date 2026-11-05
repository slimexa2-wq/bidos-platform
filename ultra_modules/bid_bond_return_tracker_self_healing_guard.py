import time
from datetime import datetime, date
import re

class BidBondReturnTracker:
    def __init__(self):
        self.bonds = {}
        self.snapshots = {}

    def _clean_amount(self, amount):
        if amount is None:
            return 0.0
        if isinstance(amount, (int, float)):
            return float(amount)
        if isinstance(amount, str):
            # Remove non-numeric characters except '.' and '-'
            cleaned = re.sub(r'[^\d.-]', '', amount)
            try:
                return float(cleaned) if cleaned else 0.0
            except ValueError:
                return 0.0
        return 0.0

    def _clean_date(self, date_input):
        if date_input is None:
            return datetime.now().date()
        if isinstance(date_input, (datetime, date)):
            return date_input if isinstance(date_input, date) and not isinstance(date_input, datetime) else date_input.date()
        if isinstance(date_input, str):
            # Try to parse some common formats or return today
            try:
                # Basic cleanup
                cleaned = re.sub(r'[^\d-]', '', date_input)
                # Try simple ISO format YYYY-MM-DD
                parts = cleaned.split('-')
                if len(parts) >= 3:
                    return date(int(parts[0][:4]), int(parts[1][:2]), int(parts[2][:2]))
                else:
                    return datetime.now().date()
            except (ValueError, TypeError):
                return datetime.now().date()
        return datetime.now().date()

    def _take_snapshot(self, bond_id):
        if bond_id in self.bonds:
            self.snapshots[bond_id] = {
                'data': self.bonds[bond_id].copy(),
                'timestamp': time.time()
            }
        else:
            self.snapshots[bond_id] = {
                'data': None,
                'timestamp': time.time()
            }

    def add_or_update_bond(self, bond_id, amount=None, return_deadline=None, status=None, **kwargs):
        self._take_snapshot(bond_id)

        cleaned_amount = self._clean_amount(amount)
        cleaned_deadline = self._clean_date(return_deadline)
        clean_status = status if status is not None else "pending"

        if bond_id in self.bonds:
            bond = self.bonds[bond_id]
            if amount is not None:
                bond['amount'] = cleaned_amount
            if return_deadline is not None:
                bond['return_deadline'] = cleaned_deadline
            if status is not None:
                bond['status'] = clean_status
            bond.update(kwargs)
        else:
            self.bonds[bond_id] = {
                'bond_id': bond_id,
                'amount': cleaned_amount,
                'return_deadline': cleaned_deadline,
                'status': clean_status,
                **kwargs
            }

        return self.bonds[bond_id]

    def get_overdue_reminders(self, current_date=None):
        if current_date is None:
            current_date = datetime.now().date()
        else:
            current_date = self._clean_date(current_date)

        overdue_bonds = []
        for bond_id, bond in self.bonds.items():
            if bond['status'] not in ['returned', 'refunded', 'cancelled']:
                if bond['return_deadline'] < current_date:
                    overdue_bonds.append(bond)

        return overdue_bonds

    def undo(self, bond_id, current_time=None):
        if current_time is None:
            current_time = time.time()

        if bond_id in self.snapshots:
            snapshot = self.snapshots[bond_id]
            if current_time - snapshot['timestamp'] <= 5.0:
                if snapshot['data'] is None:
                    # It was added in the last action, so undo means removing it
                    if bond_id in self.bonds:
                        del self.bonds[bond_id]
                else:
                    self.bonds[bond_id] = snapshot['data'].copy()

                # We consume the snapshot
                del self.snapshots[bond_id]
                return True
        return False
