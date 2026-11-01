"""Offline documentation validation. Does not execute SQL or run an Agent."""
import csv
import json
import re
from collections import deque
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
def read(path): return json.loads((ROOT/path).read_text(encoding='utf-8'))

def graph_errors(g):
    """A limited documentation linter, not the full publisher/compiler."""
    errors=[]
    ids=[n['id'] for n in g['nodes']]
    if len(set(ids)) != len(ids): return ['duplicate_id']
    nodes={n['id']:n for n in g['nodes']}
    starts=[x for x,n in nodes.items() if n['type']=='start']
    ends=[x for x,n in nodes.items() if n['type']=='end']
    if len(starts)!=1 or len(ends)!=1: errors.append('start_end_count')
    adj={x:[] for x in nodes}; rev={x:[] for x in nodes}
    for e in g['edges']:
        if e['source'] not in nodes or e['target'] not in nodes:
            errors.append('unknown_endpoint'); continue
        adj[e['source']].append(e['target']);rev[e['target']].append(e['source'])
    indegree={x:len(rev[x]) for x in nodes}; q=deque(x for x,d in indegree.items() if d==0);order=[]
    while q:
        x=q.popleft();order.append(x)
        for y in adj[x]:
            indegree[y]-=1
            if indegree[y]==0:q.append(y)
    if len(order)!=len(nodes): errors.append('cycle')
    def reach(x,edges):
        seen=set();pending=[x]
        while pending:
            y=pending.pop()
            if y not in seen:seen.add(y);pending.extend(edges[y])
        return seen
    if len(starts)==len(ends)==1:
        if reach(starts[0],adj)!=set(nodes):errors.append('unreachable_node')
        if reach(ends[0],rev)!=set(nodes):errors.append('cannot_reach_end')
    for id,n in nodes.items():
        if n['type']=='condition':
            ports={c['port'] for c in n['config']['cases']}|{n['config']['default_port']}
            actual={e.get('port','default') for e in g['edges'] if e['source']==id}
            if ports!=actual: errors.append('condition_ports')
            join=nodes.get(n['config']['join_id'])
            if not join or join['type']!='join' or join['config']['fork_id']!=id:errors.append('invalid_join_pair')
        for binding in n['inputs'].values():
            r=binding.get('ref','')
            if r.startswith('node:'):
                source=r[5:].split('#',1)[0]
                if source not in nodes:errors.append('unknown_reference')
                elif source==id or source not in reach(id,rev):errors.append('non_ancestor_reference')
    return sorted(set(errors))

def main():
    results=[]
    # JSON syntax and meta-schema checks.
    files=list((ROOT/'contracts').glob('*.json'))+list((ROOT/'examples').rglob('*.json'))
    for file in files:json.loads(file.read_text(encoding='utf-8'))
    wf=read('contracts/workflow.schema.json'); ev=read('contracts/event.schema.json')
    Draft202012Validator.check_schema(wf);Draft202012Validator.check_schema(ev)
    validator=Draft202012Validator(wf,format_checker=FormatChecker())
    for rel in ['sales','branch-approval','parallel']:
        g=read(f'examples/{rel}/workflow.json');validator.validate(g)
        assert not graph_errors(g),(rel,graph_errors(g))
    bad=read('examples/invalid/cycle.json');validator.validate(bad)
    assert 'cycle' in graph_errors(bad)
    # Independent boundary mutations: rejected by schema or the limited graph linter.
    import copy
    g=read('examples/sales/workflow.json')
    broken=copy.deepcopy(g);broken['nodes'][1]['config']={}
    assert list(validator.iter_errors(broken))
    broken=copy.deepcopy(g);broken['nodes'][1]['inputs']['file']={'ref':'node:missing#/file'}
    assert 'unknown_reference' in graph_errors(broken)
    broken=copy.deepcopy(g);broken['nodes'][1]['id']='start'
    assert 'duplicate_id' in graph_errors(broken)
    broken=read('examples/branch-approval/workflow.json');broken['edges'][1]['port']='oops'
    assert 'condition_ports' in graph_errors(broken)
    Draft202012Validator(ev,format_checker=FormatChecker()).validate(read('examples/event.json'))
    results.append('PASS JSON syntax, JSON Schema meta-validation, 3 legal workflows, cycle and 4 boundary mutations, event fixture')
    case_schema=read('contracts/evaluation-case.schema.json')
    Draft202012Validator.check_schema(case_schema)
    case_validator=Draft202012Validator(case_schema,format_checker=FormatChecker())
    for path in ['examples/evaluation/sales-case.json','examples/evaluation/unknown-write-case.json']:
        case_validator.validate(read(path))
    assert list(case_validator.iter_errors(read('examples/evaluation/invalid-empty-checker.json')))
    catalog=read('contracts/components.json')
    component_ids=set()
    for component in catalog['components']:
        assert component['id'] not in component_ids
        component_ids.add(component['id'])
        assert component['repository'].startswith('https://github.com/')
        assert component['status'] in {'confirmed_base','recommended_default','gated_candidate','development_only','source_reference'}
        if component['read_commit']:
            assert re.fullmatch('[0-9a-f]{40}',component['read_commit'])
        assert component['evidence'] and (ROOT/component['evidence']).exists()
    results.append('PASS component inventory references and read-commit format; 2 evaluation-case fixtures valid, empty checker rejected; NOT task/benchmark execution')
    # Check all local references and OpenAPI shape with an independent validator if available.
    api=read('contracts/openapi.json'); ids=[]
    def walk(v):
        if isinstance(v,dict):
            if '$ref' in v and v['$ref'].startswith('#/'):
                dest=api
                for part in v['$ref'][2:].split('/'):dest=dest[part.replace('~1','/').replace('~0','~')]
            for child in v.values():walk(child)
        elif isinstance(v,list):
            for child in v:walk(child)
    walk(api)
    for path,methods in api['paths'].items():
        for method,op in methods.items():
            ids.append(op['operationId'])
            actual={p['name'] for p in op.get('parameters',[]) if p['in']=='path'}
            assert actual==set(re.findall(r'{(.*?)}',path)),path
            assert any(code.startswith('2') for code in op['responses'])
    assert len(ids)==len(set(ids))
    from openapi_spec_validator import validate
    validate(api)
    results.append(f'PASS OpenAPI 3.1 independent validation, references and unique operation IDs ({len(ids)} operations)')
    # Parse SQL without executing it. This is not an integration/migration test.
    from pglast import parse_sql
    statements=parse_sql((ROOT/'contracts/database.sql').read_text(encoding='utf-8'))
    results.append(f'PASS PostgreSQL syntax parse ({len(statements)} statements); NOT executed against a database')
    # Independent arithmetic check of the public input fixture, plus CSV/XLSX equivalence.
    with (ROOT/'examples/sales/input.csv').open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    regions={}
    for row in rows:regions[row['region']]=regions.get(row['region'],Decimal(0))+Decimal(row['amount'])
    expected=read('examples/sales/expected.json')
    assert Decimal(expected['total'])==sum((Decimal(r['amount']) for r in rows),Decimal(0))
    assert expected['regions']=={k:f'{v:.2f}' for k,v in regions.items()}
    assert expected['order_count']==len(rows)
    from openpyxl import load_workbook
    wb=load_workbook(ROOT/'examples/sales/input.xlsx',read_only=True,data_only=False)
    values=list(wb.active.values);headers=values[0]
    assert [dict(zip(headers,row)) for row in values[1:]]==rows;wb.close()
    results.append('PASS sales CSV/XLSX equivalence and independent Decimal fixture totals; NOT a script/Agent run')
    missing=[]
    for file in ROOT.rglob('*.md'):
        if any(part in {'.venv','.deps','__pycache__'} for part in file.relative_to(ROOT).parts):continue
        content=re.sub(r'```.*?```','',file.read_text(encoding='utf-8'),flags=re.S)
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',content):
            target=target.strip('<>')
            if target.startswith(('https:','http:','mailto:','#')):continue
            target=unquote(target.split('#')[0]);target=re.sub(r':\d+$','',target)
            if target and not (file.parent/target).exists():missing.append((str(file.relative_to(ROOT)),target))
    assert not missing,missing
    results.append('PASS local Markdown file targets exist (heading anchors and remote URLs not verified)')
    print('\n'.join(results))
    (ROOT/'validation-results.txt').write_text('\n'.join(results)+'\n',encoding='utf-8')

if __name__=='__main__':main()
