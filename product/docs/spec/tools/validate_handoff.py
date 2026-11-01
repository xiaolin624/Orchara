"""Static fresh-session contract review. Not a business/runtime acceptance test."""
import json
import re
from collections import deque
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))

def main():
    results=[]
    graph=read('contracts/task-graph.json');tasks={t['id']:t for t in graph['tasks']}
    assert len(tasks)==len(graph['tasks'])
    children={id:[] for id in tasks};degrees={id:0 for id in tasks}
    for id,t in tasks.items():
        assert t['completion'] and t['artifacts'] and t['read'],id
        for d in t['depends_on']:
            assert d in tasks,(id,d)
            children[d].append(id);degrees[id]+=1
        for doc in t['read']:assert (ROOT/doc).is_file(),(id,doc)
    ready=deque(id for id,d in degrees.items() if d==0);order=[]
    while ready:
        id=ready.popleft();order.append(id)
        for child in children[id]:
            degrees[child]-=1
            if degrees[child]==0:ready.append(child)
    assert len(order)==len(tasks),'Task dependency cycle'
    assert order[0]=='D00'
    for m in graph['milestones']:
        assert m['requires'] and all(id in tasks for id in m['requires'])
    results.append(f'PASS implementation task DAG ({len(tasks)} tasks), required references and milestone targets; tasks NOT executed')
    api=read('contracts/openapi.json');operations={}
    for path,methods in api['paths'].items():
        for method,op in methods.items():
            operations[op['operationId']]=(path,method,op)
            if path.startswith('/internal/'):
                assert op['x-internal-role'] in {'supervisor','worker','maintenance'}
                assert op['security']==[{'workerMTLS':[]}]
    sql=(ROOT/'contracts/database.sql').read_text(encoding='utf-8')
    tables=set(re.findall(r'CREATE TABLE\s+(\w+)',sql,re.I))
    for target in re.findall(r'REFERENCES\s+(\w+)\s*\(',sql,re.I):assert target in tables,target
    journeys=read('contracts/journeys.json')['journeys']
    for j in journeys:
        assert j['operation_ids'] and j['criterion']
        for op in j['operation_ids']:assert op in operations,(j['id'],op)
        for table in j['tables']:assert table in tables,(j['id'],table)
    assert operations['claimJob'][2]['x-internal-role']=='supervisor'
    assert operations['confirmAttemptExit'][2]['x-internal-role']=='supervisor'
    results.append(f'PASS API/table presence for {len(journeys)} critical journeys and internal role declarations; journeys/auth NOT executed')
    schemas=api['components']['schemas']
    assert 'evaluation_report_id' not in schemas['ModelInput']['required']
    assert 'capability_card' in schemas['DraftInput']['required']
    assert 'capability_card jsonb' in sql
    model_validator=Draft202012Validator(schemas['ModelConfig'])
    model_validator.validate({'kind':'generation','protocol':'chat_compatible','context_window':4096,'max_output_tokens':256,'timeout_ms':10000,'price':{'currency':'USD','input_per_million':'0','output_per_million':'0'}})
    assert list(model_validator.iter_errors({'kind':'embedding','protocol':'embedding_compatible','timeout_ms':10000,'price':{'currency':'USD','input_per_million':'0','output_per_million':'0'}}))
    for path in ['/api/v1/conversations/{conversation_id}','/api/v1/artifacts/{artifact_id}','/api/v1/memories/{memory_id}','/api/v1/knowledge-documents/{document_id}']:
        assert pathschema(api,path,'delete','202')['$ref'].endswith('/DeletionAccepted')
    assert 'getArtifact' in operations and 'readArtifactContent' in operations
    for id in ['getDraft','getMemory']:
        _,_,op=operations[id];assert 'ETag' in op['responses']['200']['headers']
    results.append('PASS bootstrap/model-evaluation/deletion/ETag contract closure and model config boundary fixture; service behavior NOT executed')
    manifest=read('examples/execution-manifest.json')
    Draft202012Validator(read('contracts/execution-manifest.schema.json'),format_checker=FormatChecker()).validate(manifest)
    Draft202012Validator(read('contracts/workflow.schema.json'),format_checker=FormatChecker()).validate(manifest['definition'])
    for forbidden in ['run_id','snapshot_id','application_version_id','manifest_sha256','layout']:
        assert forbidden not in manifest
    results.append('PASS execution manifest envelope/embedded DSL and identity-free hashable fixture; JCS/real artifacts NOT validated')
    import yaml
    config=yaml.safe_load((ROOT/'deploy/config.example.yaml').read_text(encoding='utf-8'))
    assert config['mode']=='dev-stub'
    assert config['execution']['automatic_takeover'] is False
    assert config['sandbox']['arbitrary_upload_enabled'] is False
    results.append('PASS platform config template YAML and closed-by-default development flags; loader/deployment NOT executed')
    required=['HANDOFF.md','AGENTS.md','BOOTSTRAP.md','PROGRESS.md','IMPLEMENTATION-ORDER.md','IMPLEMENTATION-DETAILS.md','BACKGROUND-JOBS.md','COMPILER-CONTRACT.md']
    for name in required:assert (ROOT/name).is_file()
    for file in ROOT.rglob('*.md'):
        if any(x in {'.venv','.deps','__pycache__'} for x in file.relative_to(ROOT).parts):continue
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',file.read_text(encoding='utf-8')):
            assert not re.match(r'<?[A-Za-z]:[/\\]',target),(file.name,target)
    results.append('PASS self-contained entrypoints and no machine-specific absolute Markdown targets')
    print('\n'.join(results))
    (ROOT/'handoff-validation-results.txt').write_text('\n'.join(results)+'\n',encoding='utf-8')

def pathschema(api,path,method,code):
    return api['paths'][path][method]['responses'][code]['content']['application/json']['schema']

if __name__=='__main__':main()
