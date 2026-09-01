import os
import re

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Pattern for API URL ending in single quote
    p1 = r'(`\$\{process\.env\.NEXT_PUBLIC_API_URL \|\| \'http://127\.0\.0\.1:8000\'\}/[^\'`"]*)\''
    content = re.sub(p1, r'\1`', content)
    
    # Pattern for API URL ending in double quote
    p2 = r'(`\$\{process\.env\.NEXT_PUBLIC_API_URL \|\| \'http://127\.0\.0\.1:8000\'\}/[^\'`"]*)"'
    content = re.sub(p2, r'\1`', content)

    # Pattern for WS_URL which I changed from 'ws://...' to process.env...
    # Original string was 'ws://127.0.0.1:8000/ws'
    # It became process.env.NEXT_PUBLIC_WS_URL || 'ws://127.0.0.1:8000/ws'
    # Wait, the WS URL was just replaced perfectly without string concatenation!
    # I replaced: 'ws://127.0.0.1:8000/ws' exactly. Let's verify.

    with open(filepath, 'w') as f:
        f.write(content)

for root, _, files in os.walk('.'):
    for f in files:
        if f.endswith(('.ts', '.tsx')):
            fix_file(os.path.join(root, f))
print("Fixed mismatched quotes")
