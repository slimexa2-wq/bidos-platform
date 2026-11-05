import copy

def _parse_pages(page_str, max_pages, trace):
    """Parses a page string into a list of valid page integers, with self-healing."""
    pages = set()
    if not page_str:
        trace.append(f"Ignored empty page string")
        return pages

    if isinstance(page_str, int):
        page_str = str(page_str)

    if not isinstance(page_str, str):
        trace.append(f"Self-healed invalid type {type(page_str)} to string")
        page_str = str(page_str)

    parts = [p.strip() for p in page_str.replace("，", ",").replace(";", ",").split(",")]
    for part in parts:
        if not part:
            continue
        try:
            if "-" in part:
                ranges = part.split("-")
                if len(ranges) == 2:
                    start, end = int(ranges[0]), int(ranges[1])
                    if start > end:
                        trace.append(f"Self-healed inverted range: {part} -> {end}-{start}")
                        start, end = end, start
                    for p in range(start, end + 1):
                        if 1 <= p <= max_pages:
                            pages.add(p)
                        else:
                            trace.append(f"Clamped out-of-bounds page {p} in range {part}")
                else:
                    trace.append(f"Ignored malformed range: {part}")
            else:
                p = int(part)
                if 1 <= p <= max_pages:
                    pages.add(p)
                else:
                    trace.append(f"Clamped out-of-bounds page: {p}")
        except ValueError:
            trace.append(f"Ignored non-integer page string: {part}")

    return pages

def locate_seal_signature_pages(total_pages=None, config=None, previous_results=None):
    """
    Generates a precise checklist for sealing/signature pages, with full audit trace
    and differential comparison against previous results.
    """
    result = {
        "checklist": {
            "official_seal": [],
            "legal_representative_seal": [],
            "cross_page_seal": [],
            "signature": []
        },
        "audit_trace": [],
        "diff": None
    }

    # 1. Fallback for total_pages
    trace = result["audit_trace"]
    if total_pages is None or not isinstance(total_pages, int) or total_pages < 1:
        trace.append(f"total_pages missing or invalid (got {total_pages}). Defaulting to 1 (or clamping to min 1 if <1 but int).")
        if isinstance(total_pages, int) and total_pages < 1:
            total_pages = 0
        else:
            total_pages = 1

    # Extremely large documents fallback
    if total_pages > 100000:
         trace.append(f"Total pages {total_pages} exceeds maximum limit, clamping to 100000.")
         total_pages = 100000

    # 2. Fallback for config
    if not config or not isinstance(config, dict):
        trace.append("Config missing or invalid. Defaulting to official seal on first and last page.")
        config = {
            "official_seal": "1, -1",
            "legal_representative_seal": "",
            "cross_page_seal": "all",
            "signature": ""
        }

    # 3. Process each seal type
    for seal_type in ["official_seal", "legal_representative_seal", "signature"]:
        raw_val = config.get(seal_type, "")
        if raw_val is None:
            raw_val = ""

        trace.append(f"Processing {seal_type} with raw config: {raw_val}")

        # Replace -1 with total_pages
        if isinstance(raw_val, str):
            # Use negative lookbehind or simple replace to avoid replacing -1 in e.g. "8-15" (but "8-15" doesn't have "-1" unless it's "8-15" where it matches -1... wait, "8-15" has "-1" right in the middle!
            # Let's split by commas and only replace "-1" when it's exactly "-1" or has spaces.
            parts = [p.strip() for p in raw_val.split(",")]
            new_parts = []
            replaced = False
            for p in parts:
                if p == "-1":
                    new_parts.append(str(total_pages))
                    replaced = True
                else:
                    new_parts.append(p)
            if replaced:
                raw_val = ",".join(new_parts)
                trace.append(f"Resolved -1 to last page ({total_pages}) for {seal_type}")

        pages = _parse_pages(raw_val, total_pages, trace)
        result["checklist"][seal_type] = sorted(list(pages))
        trace.append(f"Finalized {seal_type} pages: {result['checklist'][seal_type]}")

    # Process cross-page seal specifically
    cross_raw = config.get("cross_page_seal", "")
    trace.append(f"Processing cross_page_seal with raw config: {cross_raw}")
    if cross_raw == "all" or cross_raw is True:
        if total_pages > 1:
            result["checklist"]["cross_page_seal"] = [f"1-{total_pages}"]
            trace.append(f"Assigned full range for cross_page_seal: 1-{total_pages}")
        else:
             trace.append(f"Document has only {total_pages} page(s), no cross_page_seal possible.")
    else:
        # custom ranges
        pages = _parse_pages(cross_raw, total_pages, trace)
        if pages:
            # We could compress it into ranges, but simple list is fine for now
            result["checklist"]["cross_page_seal"] = sorted(list(pages))
            trace.append(f"Finalized custom cross_page_seal pages: {result['checklist']['cross_page_seal']}")
        else:
             trace.append("No valid pages for cross_page_seal.")

    # 4. Generate diff if previous_results are provided
    if previous_results and isinstance(previous_results, dict) and "checklist" in previous_results:
        trace.append("Generating difference highlight against previous results.")
        diff = {}
        for k in result["checklist"].keys():
            old_set = set(previous_results["checklist"].get(k, []))
            new_set = set(result["checklist"][k])

            added = sorted(list(new_set - old_set))
            removed = sorted(list(old_set - new_set))

            if added or removed:
                diff[k] = {
                    "added": added,
                    "removed": removed
                }
        result["diff"] = diff if diff else None
    elif previous_results is not None:
        trace.append("Invalid previous_results format, skipping diff generation.")

    return result
