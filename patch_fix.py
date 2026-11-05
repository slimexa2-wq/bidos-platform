import re

with open('ultra_modules/pagination_toc_sync_verifier_audit_trace.py', 'r') as f:
    content = f.read()

# Fix the bug where custom attributes are lost because we copy from valid_toc which was created missing them
# We should keep a reference to the original item in valid_toc

content = content.replace(
"""            toc_item = {
                'title': item.get('title', ''), # preserve original title
                'page': orig_page,
                '_norm_title': norm_title
            }
""",
"""            toc_item = {
                'title': item.get('title', ''), # preserve original title
                'page': orig_page,
                '_norm_title': norm_title,
                '_original_item': item # preserve reference to original for custom attributes
            }
"""
)

content = content.replace(
"""            # Copy over any other custom attributes silently
            for k, v in item.items():
                if k not in ['title', 'page', '_norm_title']:
                    synced_item[k] = v""",
"""            # Copy over any other custom attributes silently
            for k, v in item['_original_item'].items():
                if k not in ['title', 'page']:
                    synced_item[k] = v"""
)

with open('ultra_modules/pagination_toc_sync_verifier_audit_trace.py', 'w') as f:
    f.write(content)
