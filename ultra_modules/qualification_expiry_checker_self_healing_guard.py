import time
from typing import Optional, Dict, Union, Any
from ultra_modules.qualification_expiry_checker_core_engine import check_qualification_expiry

# In-memory snapshot store for undo operations
# Format: { snapshot_id: { 'timestamp': float, 'previous_state': Any, 'current_state': Any } }
_snapshots: Dict[str, Dict[str, Any]] = {}

def check_qualification_with_self_healing(
    expiry_date: Optional[str] = None,
    current_date: Optional[str] = None,
    snapshot_id: Optional[str] = None
) -> Dict[str, Union[str, int, None, bool]]:
    """
    Check qualification expiry with self-healing for dirty data, default fallbacks, and undo snapshot support.

    Args:
        expiry_date: The expiry date string, potentially dirty or missing.
        current_date: The current date string for comparison.
        snapshot_id: A unique ID to store the operation snapshot for undoing.

    Returns:
        dict: The result containing status, days_remaining, and whether healing occurred.
    """
    # 1. Fallback for missing arguments
    original_expiry = expiry_date
    healed = False

    if expiry_date is None or not str(expiry_date).strip():
        # Default fallback: Assuming missing expiry means "invalid_format" in core engine,
        # but to provide a robust default, we might fall back to a reasonable "valid" state
        # or just pass it as empty to the core engine which will handle it.
        # But per requirements: "缺参自动设合理默认值" (Auto set reasonable default for missing param)
        # We will assume a missing expiry date for a qualification defaults to a very old date or empty,
        # Let's set it to empty so core engine says invalid_format, OR let's set a default "长期" if that's safer?
        # Actually, "reasonable default" for a qualification might be an empty string, or we pass it down.
        # Let's pass empty string. The core engine will return invalid_format.
        # Wait, if we want to "self-heal", maybe we can't heal a completely missing date unless we default to "长期".
        # Let's set it to empty string for now, which is handled gracefully by core engine.
        expiry_date = ""
        healed = True
    else:
        # 2. Self-healing for date formats
        # We need to detect if stripping removed something, but since original is checked later,
        # we can just compare original
        old_expiry = str(expiry_date)
        expiry_date = old_expiry.strip()
        if old_expiry != expiry_date:
            healed = True

        # Typos in "长期"
        if expiry_date in ["长期右效", "长其有效", "长期", "无固定期限", "长期有效", "无固定期限有效", "永远"]:
            if expiry_date not in ["长期", "无固定期限", "长期有效", "无固定期限有效"]:
                expiry_date = "长期"
                healed = True
        else:
            # Fix mixed separators like "2024.10/05" to "2024-10-05" which core engine handles but maybe partially
            # Core engine does: clean_date_str = re.sub(r'[/\.年月]', '-', expiry_str)
            # Core engine is already quite robust, but let's add self-healing for common typos like
            # missing zeros in YYYY-M-D or extra spaces

            # Remove extra spaces
            clean_date = " ".join(expiry_date.split())
            if clean_date != expiry_date:
                expiry_date = clean_date
                healed = True

    # 3. Call Core Engine
    result = check_qualification_expiry(expiry_date, current_date)
    result['healed'] = healed
    result['original_input'] = original_expiry
    result['healed_input'] = expiry_date

    # 4. Snapshot Mechanism for Undo (5-second window)
    if snapshot_id:
        _snapshots[snapshot_id] = {
            'timestamp': time.time(),
            'previous_state': None, # We don't have a real DB, so previous_state is abstract here
            'current_state': result.copy()
        }

    return result


def undo_check(snapshot_id: str) -> Dict[str, Any]:
    """
    Attempt to undo an operation using its snapshot_id.
    Valid only within a 5-second window.

    Args:
        snapshot_id: The ID of the snapshot to undo.

    Returns:
        dict: Status of the undo operation.
    """
    if snapshot_id not in _snapshots:
        return {'status': 'failed', 'reason': 'snapshot_not_found'}

    snapshot = _snapshots[snapshot_id]
    time_elapsed = time.time() - snapshot['timestamp']

    if time_elapsed > 5.0:
        # Snapshot expired
        del _snapshots[snapshot_id]
        return {'status': 'failed', 'reason': 'snapshot_expired', 'time_elapsed': time_elapsed}

    # Perform undo (in this stateless mock, we just return success and the previous state)
    # In a real app, this would revert the DB write.
    previous = snapshot['previous_state']

    # Clean up snapshot after successful undo
    del _snapshots[snapshot_id]

    return {'status': 'success', 'restored_state': previous}
