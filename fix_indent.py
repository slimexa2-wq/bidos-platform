with open('apps/api/app/services/bid_document_check.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith('def _check_amount_uppercase'):
        new_lines.append('    ' + line)
    else:
        new_lines.append(line)

with open('apps/api/app/services/bid_document_check.py', 'w') as f:
    f.writelines(new_lines)
