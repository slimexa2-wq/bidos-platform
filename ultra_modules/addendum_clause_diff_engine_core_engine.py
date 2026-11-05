import hashlib
from typing import List, Dict, Any, Optional

def clean_clause(clause: Any) -> Dict[str, Any]:
    """
    Silently cleans dirty clause data and provides reasonable defaults.
    """
    if not isinstance(clause, dict):
        # Fallback for non-dict data, wrap it in a dict
        clause_id = hashlib.md5(str(clause).encode()).hexdigest()[:8]
        return {
            "id": f"auto_{clause_id}",
            "content": str(clause) if clause is not None else "",
            "section": "unknown"
        }

    # Ensure keys are strings, values are safe strings or primitives
    cleaned = {}

    # ID handling
    raw_id = clause.get("id")
    if not raw_id:
        # Generate an ID if missing
        content_hash = hashlib.md5(str(clause.get("content", "")).encode()).hexdigest()[:8]
        cleaned["id"] = f"auto_{content_hash}"
    else:
        cleaned["id"] = str(raw_id)

    # Content handling
    raw_content = clause.get("content")
    if raw_content is None:
        cleaned["content"] = ""
    elif not isinstance(raw_content, str):
        cleaned["content"] = str(raw_content)
    else:
        cleaned["content"] = raw_content.strip()

    # Section handling
    raw_section = clause.get("section")
    if raw_section is None:
        cleaned["section"] = "unknown"
    else:
        cleaned["section"] = str(raw_section).strip()

    # Preserve other fields
    for k, v in clause.items():
        if k not in ["id", "content", "section"]:
            cleaned[str(k)] = v

    return cleaned

def clean_clauses(clauses: Any) -> List[Dict[str, Any]]:
    """
    Cleans a list of clauses, providing defaults for None or missing parameters.
    """
    if not clauses:
        return []

    if not isinstance(clauses, list):
        # Fallback if a single item is passed instead of a list
        return [clean_clause(clauses)]

    return [clean_clause(c) for c in clauses if c is not None]

def calculate_addendum_diff(old_clauses: Optional[List[Dict[str, Any]]] = None,
                            new_clauses: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Pure function algorithm: calculates clause-level diff between original and addendum clauses.
    Recalculates affected sections and produces a structured red/green diff result.
    Fully fault-tolerant with zero configurations needed.
    """
    # 1. Fallback for missing parameters and dirty data silent cleaning
    old_clean = clean_clauses(old_clauses)
    new_clean = clean_clauses(new_clauses)

    # 2. Build index of old clauses by ID
    old_index = {c["id"]: c for c in old_clean}
    new_index = {c["id"]: c for c in new_clean}

    diff_results = {
        "added": [],
        "deleted": [],
        "modified": [],
        "unchanged": [],
        "affected_sections": set()
    }

    # 3. Process new clauses (added, modified, unchanged)
    for new_id, new_c in new_index.items():
        if new_id not in old_index:
            diff_results["added"].append(new_c)
            diff_results["affected_sections"].add(new_c["section"])
        else:
            old_c = old_index[new_id]
            # Compare content and other metadata
            is_modified = False
            modifications = {}
            for k, v in new_c.items():
                if old_c.get(k) != v:
                    is_modified = True
                    modifications[k] = {"old": old_c.get(k), "new": v}

            # Check for deleted keys
            for k in old_c.keys():
                if k not in new_c:
                    is_modified = True
                    modifications[k] = {"old": old_c[k], "new": None}

            if is_modified:
                diff_results["modified"].append({
                    "id": new_id,
                    "old_clause": old_c,
                    "new_clause": new_c,
                    "changes": modifications
                })
                diff_results["affected_sections"].add(old_c["section"])
                diff_results["affected_sections"].add(new_c["section"])
            else:
                diff_results["unchanged"].append(new_c)

    # 4. Process old clauses (deleted)
    for old_id, old_c in old_index.items():
        if old_id not in new_index:
            diff_results["deleted"].append(old_c)
            diff_results["affected_sections"].add(old_c["section"])

    # Convert affected_sections set to sorted list for determinism
    diff_results["affected_sections"] = sorted(list(diff_results["affected_sections"]))

    # Generate a summary
    diff_results["summary"] = {
        "total_added": len(diff_results["added"]),
        "total_deleted": len(diff_results["deleted"]),
        "total_modified": len(diff_results["modified"]),
        "total_unchanged": len(diff_results["unchanged"]),
        "total_affected_sections": len(diff_results["affected_sections"])
    }

    return diff_results
