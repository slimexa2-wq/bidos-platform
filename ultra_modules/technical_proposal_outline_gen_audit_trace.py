import copy

def generate_technical_proposal_outline(scoring_criteria: list[dict], total_pages: int = 100) -> dict:
    """
    Generate a technical proposal outline based on scoring criteria.

    Args:
        scoring_criteria: A list of dicts, e.g., [{"title": "Technology", "weight": 40}, ...]
        total_pages: Total number of pages for the proposal.

    Returns:
        dict: A dictionary containing:
            - 'outline': List of generated chapters with title, weight, and assigned pages.
            - 'audit_log': A list of trace strings describing internal fixes/deduplications.
            - 'diff_comparator': A dict with 'original' and 'cleaned' criteria.
    """
    audit_log = []

    # 1. Fallback for total_pages
    if total_pages is None or not isinstance(total_pages, (int, float)) or total_pages <= 0:
        audit_log.append(f"Fixed 'total_pages' from {total_pages} to default 100.")
        total_pages = 100

    original_criteria = copy.deepcopy(scoring_criteria) if scoring_criteria else []
    cleaned_criteria = []

    # Handle missing or empty list
    if not scoring_criteria or not isinstance(scoring_criteria, list):
        audit_log.append("Input 'scoring_criteria' was empty or invalid. Returning empty outline.")
        return {
            "outline": [],
            "audit_log": audit_log,
            "diff_comparator": {
                "original": original_criteria,
                "cleaned": cleaned_criteria
            }
        }

    # 2. Dirty Data Self-healing & Deduplication
    title_to_weight = {}

    for idx, item in enumerate(scoring_criteria):
        if not isinstance(item, dict):
            audit_log.append(f"Item at index {idx} is not a dictionary. Skipped.")
            continue

        title = item.get("title", f"Untitled_Section_{idx}")
        if not isinstance(title, str) or not title.strip():
            audit_log.append(f"Item at index {idx} has invalid or empty title. Assigned default: Untitled_Section_{idx}.")
            title = f"Untitled_Section_{idx}"
        title = title.strip()

        raw_weight = item.get("weight", 0)
        weight = 0
        try:
            # Handle string weights, negative weights
            weight = float(raw_weight)
            if weight < 0:
                audit_log.append(f"Item '{title}' has negative weight {weight}. Reset to 0.")
                weight = 0
        except (ValueError, TypeError):
            audit_log.append(f"Item '{title}' has non-numeric weight '{raw_weight}'. Reset to 0.")
            weight = 0

        if title in title_to_weight:
            audit_log.append(f"Duplicate title '{title}' found. Merging weights: {title_to_weight[title]} + {weight}.")
            title_to_weight[title] += weight
        else:
            title_to_weight[title] = weight

    # Remove zero weight items if there are other items, unless all are zero
    total_weight = sum(title_to_weight.values())
    if total_weight == 0:
        audit_log.append("Total weight is 0. Distributing pages equally among valid sections.")
        num_sections = len(title_to_weight)
        if num_sections > 0:
            for title in title_to_weight:
                title_to_weight[title] = 100.0 / num_sections # temporary equal weights
            total_weight = 100.0

    # Build cleaned criteria
    for title, weight in title_to_weight.items():
        if weight > 0 or total_weight > 0:
            cleaned_criteria.append({"title": title, "weight": weight})

    # 3. Compute Outline
    outline = []
    accumulated_pages = 0
    for idx, item in enumerate(cleaned_criteria):
        title = item["title"]
        weight = item["weight"]

        # Calculate pages, rounding down mostly, handle last item remainder
        if total_weight > 0:
            pages = int(round((weight / total_weight) * total_pages))
        else:
            pages = 0

        if idx == len(cleaned_criteria) - 1 and total_weight > 0:
             # Last item takes the rest to avoid rounding gaps
             pages = total_pages - accumulated_pages

        # Guard against weird rounding giving negative remainder pages in extreme cases
        if pages < 0:
            pages = 0

        accumulated_pages += pages

        outline.append({
            "title": title,
            "weight": weight,
            "pages": pages
        })

    # Edge case: if we have zero sections to distribute (but total_pages > 0 and wasn't caught early)
    # The logic above ensures sum(pages) == total_pages if there's at least one item and total_weight > 0.

    return {
        "outline": outline,
        "audit_log": audit_log,
        "diff_comparator": {
            "original": original_criteria,
            "cleaned": cleaned_criteria
        }
    }
