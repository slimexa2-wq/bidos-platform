import re
from typing import List, Dict, Tuple, Any, Optional

class TocSyncVerifier:
    """
    TOC (Table of Contents) Sync Verifier with Audit Trace.
    Silently verifies and corrects TOC page numbers against actual content page numbers.
    Provides detailed audit logs for every operation.
    """

    @staticmethod
    def _normalize_title(title: Any) -> str:
        """Silently cleans and normalizes a title string."""
        if not isinstance(title, str):
            if title is None:
                return ""
            title = str(title)
        # Remove extra whitespace, make lowercase
        return re.sub(r'\s+', ' ', title).strip().lower()

    @staticmethod
    def _extract_page(page: Any) -> int:
        """Silently extracts a page number from messy inputs."""
        if isinstance(page, int):
            return max(1, page)

        if isinstance(page, str):
            # Find the first number in the string
            match = re.search(r'\d+', page)
            if match:
                return max(1, int(match.group()))

        # Default fallback
        return 1

    @classmethod
    def verify_and_sync(cls,
                        toc_entries: Optional[List[Dict[str, Any]]] = None,
                        actual_contents: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Verifies TOC entries against actual contents, calculates offsets,
        repairs TOC page numbers, and returns an audit trace.
        """
        audit_trace = []

        # Zero-configuration missing parameter fallback
        if toc_entries is None:
            audit_trace.append("Warning: toc_entries is missing (None). Fallback to empty list.")
            toc_entries = []
        if actual_contents is None:
            audit_trace.append("Warning: actual_contents is missing (None). Fallback to empty list.")
            actual_contents = []

        if not isinstance(toc_entries, list):
            audit_trace.append(f"Warning: toc_entries is {type(toc_entries)}, expected list. Fallback to empty list.")
            toc_entries = []

        if not isinstance(actual_contents, list):
            audit_trace.append(f"Warning: actual_contents is {type(actual_contents)}, expected list. Fallback to empty list.")
            actual_contents = []

        audit_trace.append("Started verification and sync.")

        # Normalize actual contents for lookup
        actual_lookup = {}
        for idx, item in enumerate(actual_contents):
            if not isinstance(item, dict):
                continue

            raw_title = item.get('title', '')
            raw_page = item.get('page', 1)

            norm_title = cls._normalize_title(raw_title)
            norm_page = cls._extract_page(raw_page)

            if norm_title:
                actual_lookup[norm_title] = norm_page
                audit_trace.append(f"Parsed actual content: '{norm_title}' at page {norm_page}")

        # Calculate offsets
        offsets = []
        valid_toc = []

        for idx, item in enumerate(toc_entries):
            if not isinstance(item, dict):
                audit_trace.append(f"Skipped invalid toc item at index {idx}.")
                continue

            raw_title = item.get('title', '')
            raw_page = item.get('page', 1)

            norm_title = cls._normalize_title(raw_title)
            orig_page = cls._extract_page(raw_page)

            toc_item = {
                'title': item.get('title', ''), # preserve original title
                'page': orig_page,
                '_norm_title': norm_title,
                '_original_item': item # preserve reference to original for custom attributes
            }
            valid_toc.append(toc_item)

            if norm_title in actual_lookup:
                actual_page = actual_lookup[norm_title]
                offset = actual_page - orig_page
                offsets.append(offset)
                audit_trace.append(f"Match found: '{norm_title}' (TOC: {orig_page}, Actual: {actual_page}, Offset: {offset})")

        # Determine majority offset
        majority_offset = 0
        if offsets:
            from collections import Counter
            counts = Counter(offsets)
            majority_offset = counts.most_common(1)[0][0]
            audit_trace.append(f"Calculated majority offset: {majority_offset}")
        else:
            audit_trace.append("No matches found. Defaulting offset to 0.")

        # Apply corrections
        synced_toc = []
        for item in valid_toc:
            norm_title = item['_norm_title']
            orig_page = item['page']

            if norm_title in actual_lookup:
                # Use actual page if perfectly matched
                new_page = actual_lookup[norm_title]
                if new_page != orig_page:
                    audit_trace.append(f"Repaired '{item['title']}': page {orig_page} -> {new_page} (exact match)")
            else:
                # Apply majority offset for unmatched
                new_page = max(1, orig_page + majority_offset)
                if new_page != orig_page:
                    audit_trace.append(f"Repaired '{item['title']}': page {orig_page} -> {new_page} (offset applied)")

            synced_item = {
                'title': item['title'],
                'page': new_page
            }
            # Copy over any other custom attributes silently
            for k, v in item['_original_item'].items():
                if k not in ['title', 'page']:
                    synced_item[k] = v

            synced_toc.append(synced_item)

        audit_trace.append("Verification and sync completed.")
        return synced_toc, audit_trace
