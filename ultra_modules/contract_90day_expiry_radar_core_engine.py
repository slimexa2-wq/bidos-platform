import datetime
import re

def parse_date(date_val):
    if not date_val:
        return None
    if isinstance(date_val, datetime.date):
        if isinstance(date_val, datetime.datetime):
            return date_val.date()
        return date_val
    if isinstance(date_val, str):
        # Replace any non-digit character with a hyphen
        date_str = re.sub(r'[^\d]', '-', date_val.strip())
        parts = [p for p in date_str.split('-') if p]
        if len(parts) >= 3:
            try:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                if year < 100:
                    year += 2000
                # Basic validation is handled by datetime.date raising ValueError
                return datetime.date(year, month, day)
            except ValueError:
                pass
    return None

def parse_number(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            # Extract valid number parts (digits, decimal, negative sign)
            cleaned = re.sub(r'[^\d.-]', '', val)
            if cleaned:
                return float(cleaned)
        except ValueError:
            pass
    return default

def parse_boolean(val, default=False):
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

def analyze_single_contract(contract, current_date=None):
    """
    Analyzes a single contract for expiry warning level and bond refund eligibility.
    Tolerates dirty data and missing parameters.
    """
    if not isinstance(contract, dict):
        # Fallback for completely invalid contract type
        contract = {}

    if current_date is None:
        current_date = datetime.date.today()
    elif isinstance(current_date, datetime.datetime):
        current_date = current_date.date()
    elif isinstance(current_date, str):
        current_date = parse_date(current_date) or datetime.date.today()

    result = contract.copy()

    # 1. Parse end_date
    end_date = parse_date(result.get('end_date'))

    # 2. Parse bond details
    bond_amount = parse_number(result.get('bond_amount', 0))
    bond_refunded_amount = parse_number(result.get('bond_refunded_amount', 0))
    is_bond_refunded = parse_boolean(result.get('is_bond_refunded', False))

    # Auto-repair bond refund status if refund amount equals or exceeds bond amount
    if bond_amount > 0 and bond_refunded_amount >= bond_amount:
        is_bond_refunded = True

    result['clean_end_date'] = end_date.isoformat() if end_date else None
    result['clean_bond_amount'] = bond_amount
    result['clean_bond_refunded'] = is_bond_refunded

    # 3. Calculate warning levels
    if end_date:
        days_remaining = (end_date - current_date).days
        result['days_remaining'] = days_remaining

        if days_remaining < 0:
            result['warning_level'] = 'expired'
        elif days_remaining <= 30:
            result['warning_level'] = '30_days'
        elif days_remaining <= 60:
            result['warning_level'] = '60_days'
        elif days_remaining <= 90:
            result['warning_level'] = '90_days'
        else:
            result['warning_level'] = 'safe'

        # 4. Bond refund eligibility
        if bond_amount <= 0:
            result['bond_refund_status'] = 'no_bond'
        elif is_bond_refunded:
            result['bond_refund_status'] = 'refunded'
        elif days_remaining <= 0:
            result['bond_refund_status'] = 'eligible_for_refund'
        else:
            result['bond_refund_status'] = 'not_eligible_yet'
    else:
        result['days_remaining'] = None
        result['warning_level'] = 'unknown'

        # If we don't know the end date, we can't reliably say if it's eligible,
        # but we can track if it has a bond or has been refunded
        if bond_amount <= 0:
            result['bond_refund_status'] = 'no_bond'
        elif is_bond_refunded:
            result['bond_refund_status'] = 'refunded'
        else:
            result['bond_refund_status'] = 'unknown'

    return result

def analyze_contracts(contracts, current_date=None):
    """
    Analyzes a list of contracts for expiry and bond warnings.
    """
    if not isinstance(contracts, list):
        if isinstance(contracts, dict):
            contracts = [contracts]
        else:
            # Complete fallback for unhandled types
            return []

    return [analyze_single_contract(c, current_date) for c in contracts]
