def process_addendum_diff(original_clauses=None, addendum_clauses=None):
    """
    Compares original clauses with addendum clauses.

    Supports missing parameters, dirty data self-healing, deduplication,
    and returns a clean diff result alongside an audit log.

    Expected clause format: dict with 'id' and 'text'.
    If 'id' is missing, it will be generated based on the text.
    """
    # 1. Missing parameter fallback
    if original_clauses is None:
        original_clauses = []
    if addendum_clauses is None:
        addendum_clauses = []

    audit_log = []
    audit_log.append("Initialization: original_clauses and addendum_clauses loaded and defaults applied.")

    # 2. Dirty data healing and standardization
    def clean_clauses(clauses, source_name):
        cleaned = []
        seen_ids = set()
        seen_texts = set()
        for idx, clause in enumerate(clauses):
            if clause is None:
                audit_log.append(f"{source_name} - Row {idx}: ignored None clause.")
                continue
            if not isinstance(clause, dict):
                audit_log.append(f"{source_name} - Row {idx}: ignored invalid clause type {type(clause)}.")
                continue

            text = clause.get('text')
            if text is None:
                audit_log.append(f"{source_name} - Row {idx}: ignored clause with None text.")
                continue

            # Heal dirty text (strip whitespace)
            clean_text = str(text).strip()
            if not clean_text:
                audit_log.append(f"{source_name} - Row {idx}: ignored clause with empty text after strip.")
                continue

            clause_id = clause.get('id')
            if clause_id is None or str(clause_id).strip() == "":
                clause_id = f"hash_{hash(clean_text)}"
                audit_log.append(f"{source_name} - Row {idx}: auto-generated id {clause_id}.")
            else:
                clause_id = str(clause_id).strip()

            # Deduplication
            if clause_id in seen_ids or clean_text in seen_texts:
                audit_log.append(f"{source_name} - Row {idx}: deduplicated clause id '{clause_id}' / text '{clean_text}'.")
                continue

            seen_ids.add(clause_id)
            seen_texts.add(clean_text)

            cleaned.append({
                'id': clause_id,
                'text': clean_text
            })

        return cleaned

    orig_cleaned = clean_clauses(original_clauses, "Original")
    add_cleaned = clean_clauses(addendum_clauses, "Addendum")

    # 3. Diffing Logic
    orig_map = {c['id']: c for c in orig_cleaned}
    add_map = {c['id']: c for c in add_cleaned}

    diff_results = []

    # Check for modified or deleted
    for orig_id, orig_clause in orig_map.items():
        if orig_id in add_map:
            add_clause = add_map[orig_id]
            if orig_clause['text'] != add_clause['text']:
                diff_results.append({
                    'id': orig_id,
                    'status': 'modified',
                    'original': orig_clause['text'],
                    'new': add_clause['text'],
                    'highlight': f"MODIFIED: [{orig_clause['text']}] -> [{add_clause['text']}]"
                })
                audit_log.append(f"Diff - Clause '{orig_id}' marked as modified.")
            else:
                diff_results.append({
                    'id': orig_id,
                    'status': 'unchanged',
                    'original': orig_clause['text'],
                    'new': add_clause['text'],
                    'highlight': "UNCHANGED"
                })
        else:
            diff_results.append({
                'id': orig_id,
                'status': 'deleted',
                'original': orig_clause['text'],
                'new': None,
                'highlight': f"DELETED: [{orig_clause['text']}]"
            })
            audit_log.append(f"Diff - Clause '{orig_id}' marked as deleted.")

    # Check for added
    for add_id, add_clause in add_map.items():
        if add_id not in orig_map:
            diff_results.append({
                'id': add_id,
                'status': 'added',
                'original': None,
                'new': add_clause['text'],
                'highlight': f"ADDED: [{add_clause['text']}]"
            })
            audit_log.append(f"Diff - Clause '{add_id}' marked as added.")

    # Calculate Impacted Sections (simple count of changes)
    impacted_count = sum(1 for d in diff_results if d['status'] in ['modified', 'added', 'deleted'])
    audit_log.append(f"Diff - Completion: {impacted_count} sections impacted.")

    return {
        "diff": diff_results,
        "impacted_count": impacted_count,
        "audit_trace": audit_log
    }
