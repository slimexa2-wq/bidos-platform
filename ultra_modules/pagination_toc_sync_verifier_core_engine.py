from collections import Counter
from typing import List, Dict, Any, Optional

def _safe_int(value: Any, default: int = 1) -> int:
    """Silently convert any value to int. Fallback to default if unable."""
    if isinstance(value, int):
        return value
    if isinstance(value, float) and not __import__("math").isnan(value):
        return int(value)
    if isinstance(value, str):
        try:
            val = int(value.strip())
            return val
        except (ValueError, TypeError):
            pass
    return default

def verify_and_sync_pagination(
    toc_entries: Any = None,
    actual_pages: Any = None,
    global_offset: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Pure function to sync and verify pagination between TOC and actual pages.
    Silently sanitizes inputs, gracefully falls back with zero configuration,
    and returns a normalized dict containing synchronized entries.
    """
    # 1. Zero-config fallback and dirty data cleaning for inputs
    if not isinstance(toc_entries, list):
        toc_entries = []

    if not isinstance(actual_pages, dict):
        actual_pages = {}

    normalized_toc = []
    for entry in toc_entries:
        if isinstance(entry, dict):
            # Ensure safe string keys and integer page values
            title = str(entry.get('title', 'Unknown Title')).strip()
            if not title:
                title = 'Unknown Title'

            # Default to page 1 if not present or invalid
            toc_page = _safe_int(entry.get('page'), default=1)
            if toc_page < 1:
                toc_page = 1

            normalized_toc.append({
                'title': title,
                'page': toc_page
            })

    normalized_actual_pages = {}
    for k, v in actual_pages.items():
        key = str(k).strip()
        if not key:
            continue
        page = _safe_int(v, default=1)
        if page < 1:
            page = 1
        normalized_actual_pages[key] = page

    # 2. Offset inference if global_offset is not directly provided or invalid
    inferred_offset = None
    if global_offset is not None:
        try:
            inferred_offset = int(global_offset)
        except (ValueError, TypeError):
            pass

    if inferred_offset is None:
        offsets = []
        for entry in normalized_toc:
            title = entry['title']
            toc_page = entry['page']
            if title in normalized_actual_pages:
                actual_page = normalized_actual_pages[title]
                offset = actual_page - toc_page
                offsets.append(offset)

        if offsets:
            # Mode (most common offset)
            inferred_offset = Counter(offsets).most_common(1)[0][0]
        else:
            inferred_offset = 0

    # 3. Synchronize and verify
    synced_results = []
    has_issues = False

    for entry in normalized_toc:
        title = entry['title']
        toc_page = entry['page']

        # Determine expected page
        if title in normalized_actual_pages:
            actual_page = normalized_actual_pages[title]
            expected_page = actual_page
            is_synced = (toc_page + inferred_offset) == actual_page
        else:
            # If actual page is missing, we project it based on the toc_page and inferred offset
            expected_page = toc_page + inferred_offset
            if expected_page < 1:
                expected_page = 1
            is_synced = True # Assuming synced if not in actual pages, just missing data

        if not is_synced:
            has_issues = True

        synced_results.append({
            'title': title,
            'original_toc_page': toc_page,
            'expected_actual_page': expected_page,
            'is_synced': is_synced,
            'suggested_correction': expected_page if not is_synced else None
        })

    return {
        'inferred_offset': inferred_offset,
        'has_issues': has_issues,
        'synced_entries': synced_results
    }
