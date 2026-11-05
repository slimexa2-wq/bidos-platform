import time
import copy
import uuid

def _clean_clause(clause):
    """
    Silent dirty data cleaning and default values assignment.
    """
    if clause is None:
        return {"id": str(uuid.uuid4()), "text": "", "section": "Unknown"}

    if isinstance(clause, str):
        return {"id": str(uuid.uuid4()), "text": clause, "section": "Unknown"}

    if not isinstance(clause, dict):
        return {"id": str(uuid.uuid4()), "text": str(clause), "section": "Unknown"}

    cleaned = {}
    cleaned["id"] = str(clause.get("id", uuid.uuid4())) if clause.get("id") else str(uuid.uuid4())
    cleaned["text"] = str(clause.get("text", ""))
    cleaned["section"] = str(clause.get("section", "Unknown")) if clause.get("section") else "Unknown"

    return cleaned

def _clean_document(doc):
    """
    Auto-fallback/silent repair for document input.
    """
    if not doc:
        return []
    if not isinstance(doc, list):
        if isinstance(doc, dict):
            if "clauses" in doc and isinstance(doc["clauses"], list):
                doc = doc["clauses"]
            else:
                doc = [doc]
        else:
            doc = [doc]

    return [_clean_clause(c) for c in doc]

def calculate_addendum_diff(original_doc, addendum_doc):
    """
    Calculates the clause-level diff between original and addendum documents
    and recalculates affected sections. Pure function, no exceptions thrown.
    """
    original = _clean_document(original_doc)
    addendum = _clean_document(addendum_doc)

    original_dict = {c["id"]: c for c in original}
    addendum_dict = {c["id"]: c for c in addendum}

    diff_result = []
    affected_sections = set()

    for o_id, o_clause in original_dict.items():
        if o_id in addendum_dict:
            a_clause = addendum_dict[o_id]
            if o_clause["text"] != a_clause["text"] or o_clause["section"] != a_clause["section"]:
                diff_result.append({
                    "id": o_id,
                    "type": "modified",
                    "original": o_clause,
                    "new": a_clause
                })
                affected_sections.add(o_clause["section"])
                affected_sections.add(a_clause["section"])
            else:
                diff_result.append({
                    "id": o_id,
                    "type": "unchanged",
                    "original": o_clause,
                    "new": a_clause
                })
        else:
            diff_result.append({
                "id": o_id,
                "type": "deleted",
                "original": o_clause,
                "new": None
            })
            affected_sections.add(o_clause["section"])

    for a_id, a_clause in addendum_dict.items():
        if a_id not in original_dict:
            diff_result.append({
                "id": a_id,
                "type": "added",
                "original": None,
                "new": a_clause
            })
            affected_sections.add(a_clause["section"])

    # Sort for deterministic output
    diff_result.sort(key=lambda x: x["id"])

    return {
        "diffs": diff_result,
        "affected_sections": sorted(list(affected_sections))
    }

def create_snapshot(original_doc, addendum_doc):
    """
    Creates a lightweight snapshot for a 5-second undo mechanism.
    """
    return {
        "snapshot_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "original_doc": copy.deepcopy(_clean_document(original_doc)),
        "addendum_doc": copy.deepcopy(_clean_document(addendum_doc))
    }

def undo_snapshot(snapshot, current_time=None):
    """
    Attempts to undo using the provided snapshot.
    If 5 seconds have passed, it silently ignores the undo and returns an error flag
    instead of throwing exceptions, adhering to the silent self-healing principle.
    """
    if not isinstance(snapshot, dict):
        return {"success": False, "reason": "Invalid snapshot format", "data": None}

    ts = snapshot.get("timestamp")
    if ts is None or not isinstance(ts, (int, float)):
        return {"success": False, "reason": "Invalid timestamp", "data": None}

    if current_time is None:
        current_time = time.time()

    if current_time - ts > 5.0:
        return {"success": False, "reason": "Snapshot expired", "data": None}

    return {
        "success": True,
        "reason": "Undo successful",
        "data": {
            "original_doc": snapshot.get("original_doc", []),
            "addendum_doc": snapshot.get("addendum_doc", [])
        }
    }
