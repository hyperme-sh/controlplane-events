"""Verify preserved runtime/schema bytes against the original extraction source."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
data=json.loads((root/'SOURCE-MANIFEST.json').read_text())
expected=set(data['files'])
actual={str(p.relative_to(root)) for p in (root/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert actual==expected, 'Runtime source inventory differs'
for path,row in (data['files'] | data.get('resource_files', {})).items():
    assert hashlib.sha256((root/path).read_bytes()).hexdigest()==row['sha256'], path
print(f"{len(expected)} runtime/schema files match reviewed source manifest")
