import copy
import re
from collections import Counter
import time

class BidConsistencySilentAuditorSelfHealingGuard:
    """
    BidConsistencySilentAuditorSelfHealingGuard
    Silent auditor and self-healing guard for bid consistency across sections.
    """
    def __init__(self):
        self._snapshots = []

    def _clean_amount(self, amount):
        if amount is None or amount == "":
            return ""
        amount_str = str(amount)
        cleaned = re.sub(r'[^\d.]', '', amount_str)
        parts = cleaned.split('.')
        if len(parts) > 2:
            cleaned = parts[0] + '.' + ''.join(parts[1:])
        try:
            return str(float(cleaned)) if cleaned else ""
        except ValueError:
            return ""

    def _clean_days(self, days):
        if days is None or days == "":
            return ""
        days_str = str(days)
        cleaned = re.sub(r'[^\d]', '', days_str)
        return cleaned

    def _clean_string(self, s):
        if s is None or s == "":
            return ""
        s = str(s)
        return re.sub(r'\s+', '', s)

    def _get_consensus(self, values, default):
        valid_values = [v for v in values if v]
        if not valid_values:
            return default
        counter = Counter(valid_values)
        return counter.most_common(1)[0][0]

    def audit_and_heal(self, document_sections):
        """
        Silently audits and heals the document sections without throwing exceptions.
        Maintains a lightweight snapshot for a potential 5-second undo mechanism.
        """
        # Save snapshot
        self._snapshots.append({
            "timestamp": time.time(),
            "data": copy.deepcopy(document_sections)
        })

        if not isinstance(document_sections, list):
            # Extreme boundary: gracefully return an empty list if input is not a list
            return []

        amounts = []
        amount_capitals = []
        days_list = []
        pm_names = []
        pm_certs = []

        for section in document_sections:
            if not isinstance(section, dict):
                continue
            amounts.append(self._clean_amount(section.get('amount')))
            amount_capitals.append(self._clean_string(section.get('amount_capital')))
            days_list.append(self._clean_days(section.get('days')))
            pm_names.append(self._clean_string(section.get('pm_name')))
            pm_certs.append(self._clean_string(section.get('pm_cert')))

        # Consensus resolution with zero-config fallbacks
        consensus_amount = self._get_consensus(amounts, "0.0")
        consensus_amount_capital = self._get_consensus(amount_capitals, "零元整")
        consensus_days = self._get_consensus(days_list, "0")
        consensus_pm_name = self._get_consensus(pm_names, "未指定")
        consensus_pm_cert = self._get_consensus(pm_certs, "未指定")

        healed_sections = []
        for section in document_sections:
            if not isinstance(section, dict):
                healed_sections.append(section)
                continue

            healed = copy.deepcopy(section)
            healed['amount'] = consensus_amount
            healed['amount_capital'] = consensus_amount_capital
            healed['days'] = consensus_days
            healed['pm_name'] = consensus_pm_name
            healed['pm_cert'] = consensus_pm_cert
            healed_sections.append(healed)

        return healed_sections

    def undo(self):
        """
        One-click undo (revert to the last snapshot).
        Assumes the caller might invoke this within 5 seconds for UI,
        but backend fully supports infinite-time undo via stack.
        """
        if not self._snapshots:
            return []

        snapshot = self._snapshots.pop()
        return snapshot["data"]
