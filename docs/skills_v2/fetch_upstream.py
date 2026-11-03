#!/usr/bin/env python3
"""Download specifically reviewed upstream GitHub SKILL.md files into a quarantine folder.
Only for manual skill review. It never installs/executes external code or changes library.
"""
from pathlib import Path
import hashlib, urllib.request, yaml
HERE=Path(__file__).resolve().parent
OUT=HERE/'upstream_raw';OUT.mkdir(exist_ok=True)
REPO=yaml.safe_load((HERE/'external_sources.yaml').read_text(encoding='utf-8'))['sources']
def git_sha(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
for item in REPO:
    if item.get('handling') not in ('researched_adapted_to_native_yaml','researched_integrated_existing'):
        continue
    url=item['url'].replace('github.com/','raw.githubusercontent.com/').replace('/blob/main/','/main/')
    target=OUT/(item['skill']+'.SKILL.md')
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'PaperAgent-Skill-Audit/1'}),timeout=20) as f: raw=f.read()
        actual=git_sha(raw)
        if item.get('sha') and actual!=item['sha']:
            raise RuntimeError(f'Git blob SHA mismatch: actual {actual}, expected {item["sha"]}; review upstream update before using')
        target.write_bytes(raw)
        print('Downloaded',target)
    except Exception as e:
        print('SKIPPED',item['skill'],str(e))
