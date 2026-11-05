import json

def _clean_page_numbers(pages_data):
    """
    Silently clean dirty data for page numbers.
    - Handles missing or None
    - Handles lists containing strings, floats, ints, or mixed
    - Handles negative numbers by ignoring them or abs them? Usually page numbers are positive. We will ignore non-positive numbers.
    - Removes duplicates
    - Sorts the final list
    """
    if not pages_data:
        return []

    if not isinstance(pages_data, list):
        pages_data = [pages_data]

    clean_pages = set()
    for item in pages_data:
        if item is None:
            continue
        try:
            # Handle float/string to int conversion
            val = int(float(item))
            if val > 0:
                clean_pages.add(val)
        except (ValueError, TypeError):
            # Try splitting by comma if it's a string like '1, 2, 3'
            if isinstance(item, str):
                for part in item.replace('，', ',').split(','):
                    try:
                        val = int(float(part.strip()))
                        if val > 0:
                            clean_pages.add(val)
                    except (ValueError, TypeError):
                        pass

    return sorted(list(clean_pages))

def generate_locator_card(data: dict = None) -> dict:
    """
    Generates a simple, plain-text summary and visualization card for seal/signature page numbers.

    Args:
        data (dict): Dictionary containing page numbers for different seal types.
                     Expected keys: 'official_seal', 'legal_rep_seal', 'cross_page_seal', 'signature'

    Returns:
        dict: A dictionary containing 'plain_text', 'html', and 'json' representations.
    """
    if data is None:
        data = {}

    # Extract and clean data, applying empty list as default for missing keys
    official_seal = _clean_page_numbers(data.get('official_seal', []))
    legal_rep_seal = _clean_page_numbers(data.get('legal_rep_seal', []))
    cross_page_seal = _clean_page_numbers(data.get('cross_page_seal', []))
    signature = _clean_page_numbers(data.get('signature', []))

    # Format helpers
    def format_pages(pages):
        return ", ".join(map(str, pages)) if pages else "无"

    # Generate outputs
    official_seal_str = format_pages(official_seal)
    legal_rep_seal_str = format_pages(legal_rep_seal)
    cross_page_seal_str = format_pages(cross_page_seal)
    signature_str = format_pages(signature)

    # 1. Plain Text Summary
    plain_text = (
        f"【盖章/签字页码清单】\n"
        f"公章：{official_seal_str}\n"
        f"法人章：{legal_rep_seal_str}\n"
        f"骑缝章：{cross_page_seal_str}\n"
        f"签字：{signature_str}"
    )

    # 2. HTML Card
    html = f"""
<div class="seal-locator-card" style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; font-family: sans-serif; max-width: 400px;">
    <h3 style="margin-top: 0; color: #333; font-size: 18px; border-bottom: 2px solid #f0f0f0; padding-bottom: 8px;">盖章/签字页码清单</h3>
    <ul style="list-style-type: none; padding: 0; margin: 0;">
        <li style="margin-bottom: 8px;"><strong style="color: #555;">公章：</strong><span style="color: #1a73e8;">{official_seal_str}</span></li>
        <li style="margin-bottom: 8px;"><strong style="color: #555;">法人章：</strong><span style="color: #1a73e8;">{legal_rep_seal_str}</span></li>
        <li style="margin-bottom: 8px;"><strong style="color: #555;">骑缝章：</strong><span style="color: #1a73e8;">{cross_page_seal_str}</span></li>
        <li style="margin-bottom: 0;"><strong style="color: #555;">签字：</strong><span style="color: #1a73e8;">{signature_str}</span></li>
    </ul>
</div>
    """.strip()

    # 3. JSON Data
    json_data = {
        "official_seal": official_seal,
        "legal_rep_seal": legal_rep_seal,
        "cross_page_seal": cross_page_seal,
        "signature": signature
    }

    return {
        "plain_text": plain_text,
        "html": html,
        "json": json_data
    }
