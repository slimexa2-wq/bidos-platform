with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'def _check_sub_amounts' in line:
        print(f"Line {i} spaces: {len(line) - len(line.lstrip())}")
    if 'def _check_amount_uppercase' in line:
        print(f"Line {i} spaces: {len(line) - len(line.lstrip())}")
