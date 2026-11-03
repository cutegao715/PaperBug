#!/usr/bin/env python3
"""Preview or rewrite legacy skill references in JSON/YAML workflow configuration.
Never auto-edits code, DB or original files; backup before --write.
"""
import argparse, json, yaml
from pathlib import Path
HERE=Path(__file__).resolve().parent
mapping=json.loads((HERE/'legacy_skill_mapping.json').read_text(encoding='utf-8'))
def update(node):
    n=0
    if isinstance(node,dict):
        if isinstance(node.get('skill_id'),str) and node['skill_id'] in mapping:
            new=mapping[node['skill_id']]
            if new!=node['skill_id']: node['skill_id']=new;n+=1
        for v in node.values():n+=update(v)
    elif isinstance(node,list):
        for v in node:n+=update(v)
    return n
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('file',type=Path);a.add_argument('--write',action='store_true');args=a.parse_args()
    p=args.file;original=p.read_text(encoding='utf-8');is_json=p.suffix.lower()=='.json';obj=json.loads(original) if is_json else yaml.safe_load(original)
    n=update(obj);print(f'{p}: {n} references changed')
    if args.write and n:
        backup=p.with_suffix(p.suffix+'.bak');backup.write_text(original,encoding='utf-8')
        p.write_text(json.dumps(obj,ensure_ascii=False,indent=2) if is_json else yaml.safe_dump(obj,allow_unicode=True,sort_keys=False),encoding='utf-8')
        print(f'Backup: {backup}')
