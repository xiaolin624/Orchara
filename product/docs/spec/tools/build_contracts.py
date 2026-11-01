"""Generate documentation contracts and fixtures; no product runtime is implemented."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def save(path, value):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

S = lambda **kw: dict(type='string', **kw)
I = lambda **kw: dict(type='integer', **kw)
def obj(properties=None, required=None, extra=False):
    return dict(type='object', properties=properties or {}, required=required or [], additionalProperties=extra)
def arr(items, **kw): return dict(type='array', items=items, **kw)
UUID = S(format='uuid')
binding = {'oneOf': [obj({'literal': {}}, ['literal']), obj({'ref': S(pattern=r'^(input|node:[A-Za-z][A-Za-z0-9_-]*)#(/.*)?$')}, ['ref'])]}
node_types = ['start','end','model','agent','python','call_app','retrieve','condition','join','approval']
configs = {
    'start': obj(), 'end': obj(),
    'model': obj({'model_profile_version': UUID, 'prompt_template': S(), 'response_mode': S(enum=['text','json'])}, ['model_profile_version','prompt_template','response_mode']),
    'agent': obj({'model_profile_version': UUID, 'instructions': S(), 'tool_versions': arr(UUID, uniqueItems=True), 'subapp_versions': arr(UUID, uniqueItems=True), 'max_turns': I(minimum=1,maximum=50)}, ['model_profile_version','instructions','tool_versions','subapp_versions','max_turns']),
    'python': obj({'tool_version': UUID}, ['tool_version']),
    'call_app': obj({'application_version': UUID}, ['application_version']),
    'retrieve': obj({'knowledge_base_ids': arr(UUID,minItems=1,uniqueItems=True), 'top_k': I(minimum=1,maximum=20)}, ['knowledge_base_ids','top_k']),
    'condition': obj({'join_id': S(), 'default_port': S(), 'cases': arr(obj({'input': S(), 'op': S(enum=['eq','ne','gt','gte','lt','lte','in','exists']), 'value': {}, 'port': S()}, ['input','op','port']), minItems=1)}, ['join_id','default_port','cases']),
    'join': obj({'mode': S(enum=['all_active']), 'fork_id': S(), 'required_sources': arr(S(),uniqueItems=True), 'optional_sources': arr(S(),uniqueItems=True)}, ['mode','fork_id','required_sources','optional_sources']),
    'approval': obj({'title': S(), 'expires_seconds': I(minimum=60,maximum=86400)}, ['title','expires_seconds'])
}
node = obj({'id': S(pattern='^[A-Za-z][A-Za-z0-9_-]{0,63}$'), 'type': S(enum=node_types), 'inputs': dict(type='object',additionalProperties=binding), 'output_schema': dict(type='object'), 'config': dict(type='object')}, ['id','type','inputs','output_schema','config'])
node['allOf'] = [{'if': {'properties': {'type': {'const': kind}}}, 'then': {'properties': {'config': conf}}} for kind, conf in configs.items()]
workflow = obj({'dsl_version': {'const':'1.0'}, 'input_schema': dict(type='object'), 'output_schema': dict(type='object'), 'nodes': arr(node,minItems=2,maxItems=100), 'edges': arr(obj({'source': S(), 'target': S(), 'port': S()}, ['source','target']),maxItems=400), 'policy': obj({'max_active_seconds': I(minimum=1,maximum=3600), 'max_child_depth': I(minimum=0,maximum=3), 'max_child_calls': I(minimum=0,maximum=12), 'budget_limit':S(pattern=r'^\d+(\.\d{1,8})?$'),'currency':S(minLength=3,maxLength=3)}, ['max_active_seconds','max_child_depth','max_child_calls','budget_limit','currency'])}, ['dsl_version','input_schema','output_schema','nodes','edges','policy'])
workflow.update({'$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'urn:agent-platform:workflow:1.0', 'title':'Agent platform execution definition'})
save('contracts/workflow.schema.json', workflow)

event = obj({'schema_version': {'const':'1.0'}, 'event_id': UUID, 'run_id': UUID, 'seq': I(minimum=1), 'revision': I(minimum=1), 'type': S(enum=['run.status','node.status','approval.required','artifact.ready','usage.updated']), 'created_at': S(format='date-time'), 'payload': dict(type='object')}, ['schema_version','event_id','run_id','seq','revision','type','created_at','payload'])
payloads = {
 'run.status': obj({'state': S(), 'error_code': S()}, ['state']),
 'node.status': obj({'node_id': S(), 'state': S(), 'attempt_no': I(minimum=1)}, ['node_id','state','attempt_no']),
 'approval.required': obj({'approval_id': UUID, 'expires_at': S(format='date-time')}, ['approval_id','expires_at']),
 'artifact.ready': obj({'artifact_id': UUID, 'media_type': S()}, ['artifact_id','media_type']),
 'usage.updated': obj({'currency': S(), 'charged': S(pattern=r'^\d+(\.\d+)?$'), 'reserved': S(pattern=r'^\d+(\.\d+)?$')}, ['currency','charged','reserved'])
}
event['allOf'] = [{'if': {'properties': {'type': {'const': k}}}, 'then': {'properties': {'payload': v}}} for k,v in payloads.items()]
event['$schema']='https://json-schema.org/draft/2020-12/schema'
save('contracts/event.schema.json',event)

def ref(name): return {'$ref':'#/components/schemas/'+name}
run_states = ['queued','running','waiting_approval','waiting_children','cancelling','recovery_pending','needs_reconciliation','succeeded','failed','cancelled']
components = {
 'Error': obj({'error': obj({'code':S(),'message':S(),'request_id':S(),'details':dict(type='object',additionalProperties=True)}, ['code','message','request_id'])}, ['error']),
 'Accepted': obj({'id':UUID,'state':S()}, ['id','state']),
 'Session': obj({'principal_id':UUID,'csrf_token':S(),'expires_at':S(format='date-time')}, ['principal_id','csrf_token','expires_at']),
 'Application': obj({'id':UUID,'name':S(),'active_version_id':{'type':['string','null'],'format':'uuid'}}, ['id','name','active_version_id']),
 'DraftInput': obj({'definition': dict(type='object',description='Must validate workflow.schema.json and semantic publish checks'), 'layout':dict(type='object',additionalProperties=True),'capability_card':obj({'purpose':S(),'includes':arr(S()),'excludes':arr(S()),'required_inputs':arr(S()),'expected_outputs':arr(S()),'examples':arr(S(),minItems=2)}, ['purpose','includes','excludes','required_inputs','expected_outputs','examples'])}, ['definition','layout','capability_card']),
 'Version': obj({'id':UUID,'application_id':UUID,'version_no':I(minimum=1),'manifest_sha256':S(),'manifest':dict(type='object',additionalProperties=True)}, ['id','application_id','version_no','manifest_sha256','manifest']),
 'RequestInput': obj({'conversation_id':UUID,'message':S(minLength=1,maxLength=32000),'artifact_ids':arr(UUID,maxItems=5,uniqueItems=True)}, ['conversation_id','message','artifact_ids']),
 'Request': obj({'id':UUID,'state':S(enum=['queued','routing','awaiting_input','awaiting_selection','dispatched','cancelled','failed']),'run_id':{'type':['string','null'],'format':'uuid'},'interaction':{'type':['object','null'],'additionalProperties':True}}, ['id','state','run_id','interaction']),
 'InteractionReply': obj({'interaction_id':UUID,'message':S(),'candidate_version_id':UUID,'artifact_ids':arr(UUID,maxItems=5)}, ['interaction_id']),
 'Run': obj({'id':UUID,'state':S(enum=run_states),'revision':I(minimum=1),'application_version_id':UUID,'parent_run_id':{'type':['string','null'],'format':'uuid'},'output_artifact_id':{'type':['string','null'],'format':'uuid'},'error_code':{'type':['string','null']}}, ['id','state','revision','application_version_id','parent_run_id','output_artifact_id','error_code']),
 'UploadInput': obj({'media_type':S(),'size_bytes':I(minimum=1,maximum=20971520),'purpose':S(enum=['task_input','tool_source','knowledge'])}, ['media_type','size_bytes','purpose']),
 'Upload': obj({'artifact_id':UUID,'upload_url':S(format='uri'),'expires_at':S(format='date-time')}, ['artifact_id','upload_url','expires_at']),
 'Artifact': obj({'id':UUID,'state':S(),'media_type':S(),'size_bytes':I(minimum=0)}, ['id','state','media_type','size_bytes']),
 'Download': obj({'url':S(format='uri'),'expires_at':S(format='date-time')}, ['url','expires_at']),
 'Approval': obj({'id':UUID,'run_id':UUID,'state':S(enum=['pending','approved','rejected','expired','invalidated']),'proposal_artifact_id':UUID,'params_sha256':S(),'expires_at':S(format='date-time')}, ['id','run_id','state','proposal_artifact_id','params_sha256','expires_at']),
 'Decision': obj({'decision':S(enum=['approve','reject']),'params_sha256':S()}, ['decision','params_sha256']),
 'MemoryInput': obj({'application_id':UUID,'text':S(minLength=1,maxLength=4000),'confirmed':{'const':True}}, ['application_id','text','confirmed']),
 'Memory': obj({'id':UUID,'application_id':UUID,'revision':I(minimum=1),'text':S()}, ['id','application_id','revision','text']),
 'ToolInput': obj({'source_artifact_id':UUID,'input_schema':dict(type='object'),'output_schema':dict(type='object'),'dependency_lock':dict(type='object',additionalProperties=True),'risk_class':S(enum=['pure','read','write'])}, ['source_artifact_id','input_schema','output_schema','dependency_lock','risk_class']),
 'ModelInput': obj({'name':S(),'provider':S(),'model_id':S(),'endpoint_ref':S(),'secret_ref':S(),'config':dict(type='object',additionalProperties=True),'data_destination':S(),'evaluation_report_id':UUID}, ['name','provider','model_id','endpoint_ref','secret_ref','config','data_destination','evaluation_report_id']),
 'Lease': obj({'job_id':UUID,'run_id':UUID,'attempt_token':UUID,'lease_deadline':S(format='date-time'),'manifest':dict(type='object',additionalProperties=True),'protocol_version':{'const':'1.0'}}, ['job_id','run_id','attempt_token','lease_deadline','manifest','protocol_version']),
 'Heartbeat': obj({'attempt_token':UUID,'execution_unit_id':S()}, ['attempt_token','execution_unit_id']),
 'HeartbeatReply': obj({'lease_deadline':S(format='date-time'),'cancel_requested':dict(type='boolean'),'policy_revision':I(),'budget_remaining':S()}, ['lease_deadline','cancel_requested','policy_revision','budget_remaining']),
 'WorkerEvent': obj({'event_id':UUID,'local_sequence':I(minimum=1),'type':S(),'payload':dict(type='object',additionalProperties=True)}, ['event_id','local_sequence','type','payload']),
 'WorkerResult': obj({'attempt_token':UUID,'event_id':UUID,'suggested_state':S(enum=['succeeded','failed','waiting_approval','waiting_children','needs_reconciliation']),'output_artifact_id':UUID,'checkpoint_id':S(),'error_code':S()}, ['attempt_token','event_id','suggested_state']),
 'CapabilityInput': obj({'attempt_token':UUID,'operation_id':UUID,'logical_key':S(),'capability_version':S(),'params':dict(type='object',additionalProperties=True)}, ['attempt_token','operation_id','logical_key','capability_version','params']),
 'Reconciliation': obj({'conclusion':S(enum=['confirmed_success','confirmed_not_sent','confirmed_failure']),'evidence_artifact_id':UUID}, ['conclusion','evidence_artifact_id']),
 'Report': obj({'id':UUID,'status':S(enum=['passed','failed','not_run']),'manifest_sha256':S(),'result_artifact_id':UUID}, ['id','status','manifest_sha256','result_artifact_id'])
}
components['Heartbeat']['properties']['checkpoint_connections']=arr(obj({'pid':I(minimum=1),'backend_start':S(format='date-time'),'application_name':S()},['pid','backend_start','application_name']))
components['Run']['properties']['kind']=S(enum=['published','debug'])
components['Run']['properties']['application_version_id']={'type':['string','null'],'format':'uuid'}
components['Run']['properties']['execution_snapshot_id']=UUID
components['Run']['required']+=['kind','execution_snapshot_id']
components['WorkerResult']['allOf']=[
 {'if':{'properties':{'suggested_state':{'enum':['waiting_approval','waiting_children']}}},'then':{'required':['checkpoint_id']}},
 {'if':{'properties':{'suggested_state':{'const':'succeeded'}}},'then':{'required':['output_artifact_id']}},
 {'if':{'properties':{'suggested_state':{'const':'failed'}}},'then':{'required':['error_code']}}
]
paths={}
def operation(path,method,name,body=None,result=None,status=200,security='session',headers=(),description=''):
    params=[]
    import re
    for part in re.findall(r'{(.*?)}',path): params.append({'name':part,'in':'path','required':True,'schema':UUID})
    for h in headers: params.append({'name':h,'in':'header','required':True,'schema':S(minLength=1)})
    responses={str(status): {'description':'Success'}}
    if result: responses[str(status)]['content']={'application/json':{'schema':ref(result) if isinstance(result,str) else result}}
    for code in [400,401,403,404,409,422,429,503]: responses[str(code)]={'description':'See Error.code and API.md','content':{'application/json':{'schema':ref('Error')}}}
    op={'operationId':name,'description':description or name,'parameters':params,'responses':responses,'security':[] if security=='none' else ([{'workerMTLS':[]}] if security=='worker' else [{'sessionCookie':[]},{'apiKey':[]}])}
    if body: op['requestBody']={'required':True,'content':{'application/json':{'schema':ref(body) if isinstance(body,str) else body}}}
    if security=='session' and method not in ['get','head']: op['description'] += ' Cookie sessions also require X-CSRF-Token. Scoped API keys require object ACL.'
    if 'If-Match' in headers:
        for code in [412,428]: op['responses'][str(code)]={'description':'ETag precondition failed or missing','content':{'application/json':{'schema':ref('Error')}}}
    paths.setdefault(path,{})[method]=op
P='/api/v1'
operation(P+'/auth/login','post','login',obj({'login':S(),'password':S()},['login','password']),'Session',security='none',description='Local account login; creates secure session cookie. Rate limited.')
operation(P+'/auth/logout','post','logout',status=204)
operation(P+'/conversations','post','createConversation',result='Accepted',status=201)
operation(P+'/conversations/{conversation_id}','delete','deleteConversation',result='Accepted',status=202)
operation(P+'/applications','post','createApplication',obj({'name':S(minLength=1)},['name']),'Application',201)
operation(P+'/applications','get','listApplications',result=obj({'items':arr(ref('Application')),'next_cursor':{'type':['string','null']}},['items','next_cursor']))
operation(P+'/applications/{application_id}/draft','get','getDraft',result='DraftInput')
operation(P+'/applications/{application_id}/draft','put','saveDraft','DraftInput','Accepted',headers=['If-Match'])
operation(P+'/applications/{application_id}/validate','post','validateApplication',result=obj({'valid':dict(type='boolean'),'errors':arr(obj({'node_id':S(),'code':S(),'pointer':S(),'message':S()},['code','pointer','message']))},['valid','errors']))
operation(P+'/applications/{application_id}/debug-runs','post','debugDraft',obj({'draft_revision':I(minimum=1),'input':dict(type='object',additionalProperties=True),'artifact_ids':arr(UUID,maxItems=5)},['draft_revision','input','artifact_ids']),'Run',202,headers=['Idempotency-Key'])
operation(P+'/applications/{application_id}/evaluations','post','evaluateDraft',obj({'draft_revision':I(minimum=1),'dataset_artifact_id':UUID},['draft_revision','dataset_artifact_id']),'Accepted',202,headers=['Idempotency-Key'],description='Runs a trusted evaluator; callers cannot attest their own passed status.')
operation(P+'/applications/{application_id}/submit','post','submitApplication',obj({'expected_manifest_sha256':S(),'evaluation_report_id':UUID},['expected_manifest_sha256','evaluation_report_id']),'Accepted',202,headers=['Idempotency-Key'])
operation(P+'/reviews/{review_id}/decision','post','reviewPublication',obj({'decision':S(enum=['approve','reject']),'manifest_sha256':S(),'reason':S()},['decision','manifest_sha256','reason']),'Accepted',headers=['Idempotency-Key'])
operation(P+'/versions/{version_id}','get','getVersion',result='Version')
operation(P+'/versions/{version_id}/revoke','post','revokeVersion',obj({'reason':S(),'stop_existing':dict(type='boolean')},['reason','stop_existing']),'Accepted')
operation(P+'/requests','post','submitRequest','RequestInput','Request',202,headers=['Idempotency-Key'])
operation(P+'/requests/{request_id}','get','getRequest',result='Request')
operation(P+'/requests/{request_id}/reply','post','replyInteraction','InteractionReply','Request',202,headers=['Idempotency-Key'])
operation(P+'/requests/{request_id}/cancel','post','cancelRequest',result='Accepted',status=202)
operation(P+'/runs/{run_id}','get','getRun',result='Run')
operation(P+'/runs/{run_id}/cancel','post','cancelRun',result='Run',status=202)
operation(P+'/runs/{run_id}/events','get','runEvents',description='Durable event replay. Last-Event-ID is local to this Run. Ephemeral model deltas have no replay guarantee.')
paths[P+'/runs/{run_id}/events']['get']['parameters'].append({'name':'Last-Event-ID','in':'header','required':False,'schema':S(pattern='^\\d+$')})
paths[P+'/runs/{run_id}/events']['get']['responses']['200']={'description':'SSE','content':{'text/event-stream':{'schema':S()}}}
paths[P+'/runs/{run_id}/events']['get']['responses']['410']={'description':'Cursor expired; fetch current authorized snapshot','content':{'application/json':{'schema':ref('Error')}}}
operation(P+'/artifacts/uploads','post','createUpload','UploadInput','Upload',201)
operation(P+'/artifacts/{artifact_id}/complete','post','completeUpload',obj({'sha256':S(pattern='^[0-9a-f]{64}$')},['sha256']),'Artifact',202)
operation(P+'/artifacts/{artifact_id}/download','get','downloadArtifact',result='Download')
operation(P+'/artifacts/{artifact_id}','delete','deleteArtifact',result='Accepted',status=202)
operation(P+'/approvals/{approval_id}','get','getApproval',result='Approval')
operation(P+'/approvals/{approval_id}/decision','post','decideApproval','Decision','Approval',headers=['Idempotency-Key'])
operation(P+'/operations/{operation_id}/reconcile','post','reconcileOperation','Reconciliation','Accepted',headers=['Idempotency-Key'])
operation(P+'/memories','post','createMemory','MemoryInput','Memory',201)
operation(P+'/memories','get','listMemories',result=arr(ref('Memory')))
operation(P+'/memories/{memory_id}','patch','updateMemory',obj({'text':S(minLength=1),'confirmed':{'const':True}},['text','confirmed']),'Memory',headers=['If-Match'])
operation(P+'/memories/{memory_id}','delete','deleteMemory',result='Accepted',status=202)
operation(P+'/tools','post','submitTool','ToolInput','Accepted',202)
operation(P+'/tools/{tool_version_id}','get','getTool',result=obj({'id':UUID,'state':S(),'image_digest':{'type':['string','null']}},['id','state','image_digest']))
operation(P+'/tools/{tool_version_id}/review','post','reviewTool',obj({'decision':S(enum=['approve','reject']),'manifest_sha256':S(),'reason':S()},['decision','manifest_sha256','reason']),'Accepted',headers=['Idempotency-Key'])
operation(P+'/knowledge-bases','post','createKnowledgeBase',obj({'name':S()},['name']),'Accepted',201)
operation(P+'/knowledge-bases/{knowledge_base_id}/documents','post','ingestDocument',obj({'source_artifact_id':UUID},['source_artifact_id']),'Accepted',202)
operation(P+'/knowledge-documents/{document_id}','delete','deleteDocument',result='Accepted',status=202)
operation(P+'/models','post','registerModel','ModelInput','Accepted',201)
operation(P+'/evaluation-reports/{report_id}','get','getEvaluationReport',result='Report')
operation(P+'/diagnostic-grants','post','grantDiagnostics',obj({'run_id':UUID,'grantee_id':UUID,'purposes':arr(S(),minItems=1),'expires_at':S(format='date-time')},['run_id','grantee_id','purposes','expires_at']),'Accepted',201)
operation(P+'/diagnostic-grants/{grant_id}','delete','revokeDiagnostics',status=204)
operation(P+'/admin/users','post','inviteUser',obj({'login':S(),'display_name':S(),'roles':arr(S(enum=['admin','creator','user']))},['login','display_name','roles']),'Accepted',201)
operation(P+'/admin/grants','post','grantResource',obj({'principal_id':UUID,'resource_type':S(),'resource_id':UUID,'action':S(),'expires_at':S(format='date-time')},['principal_id','resource_type','resource_id','action']),'Accepted',201)
operation(P+'/admin/grants/{grant_id}','delete','revokeResourceGrant',status=204)
operation(P+'/admin/service-accounts','post','createServiceAccount',obj({'display_name':S(),'responsible_user_id':UUID},['display_name','responsible_user_id']),'Accepted',201)
operation(P+'/admin/api-keys','post','createApiKey',obj({'principal_id':UUID,'scopes':arr(S(),minItems=1),'expires_at':S(format='date-time')},['principal_id','scopes','expires_at']),obj({'id':UUID,'secret':S()},['id','secret']),201)
operation(P+'/admin/api-keys/{key_id}','delete','revokeApiKey',status=204)
W='/internal/v1'
operation(W+'/jobs/claim','post','claimJob',obj({'worker_id':S(),'supported_compilers':arr(S(),minItems=1),'execution_unit_id':S()},['worker_id','supported_compilers','execution_unit_id']),'Lease',security='worker')
paths[W+'/jobs/claim']['post']['responses']['204']={'description':'No available compatible job'}
operation(W+'/runs/{run_id}/heartbeat','post','heartbeat','Heartbeat','HeartbeatReply',security='worker')
operation(W+'/runs/{run_id}/events','post','submitWorkerEvents',obj({'attempt_token':UUID,'events':arr(ref('WorkerEvent'),minItems=1,maxItems=100)},['attempt_token','events']),'Accepted',security='worker')
operation(W+'/runs/{run_id}/result','post','submitWorkerResult','WorkerResult','Run',security='worker')
operation(W+'/runs/{run_id}/capabilities','post','invokeCapability','CapabilityInput',obj({'operation_id':UUID,'state':S(),'receipt_artifact_id':{'type':['string','null'],'format':'uuid'}},['operation_id','state','receipt_artifact_id']),security='worker')
operation(W+'/runs/{run_id}/children','post','spawnChild',obj({'attempt_token':UUID,'logical_key':S(),'application_version_id':UUID,'input':dict(type='object',additionalProperties=True)},['attempt_token','logical_key','application_version_id','input']),'Run',202,security='worker')
operation(W+'/attempts/{attempt_id}/exited','post','confirmAttemptExit',obj({'execution_unit_id':S(),'evidence':dict(type='object',additionalProperties=True)},['execution_unit_id','evidence']),'Accepted',security='worker',description='Only supervisor identity can attest exit; ordinary worker identity is insufficient.')
operation(W+'/runs/{run_id}/artifacts','post','registerRunArtifact',obj({'attempt_token':UUID,'artifact_id':UUID,'relation':S()},['attempt_token','artifact_id','relation']),'Artifact',security='worker')
operation(W+'/runs/{run_id}/artifact-uploads','post','createWorkerUpload',obj({'attempt_token':UUID,'media_type':S(),'size_bytes':I(minimum=0,maximum=20971520),'purpose':S(enum=['node_output','final_output','log','receipt'])},['attempt_token','media_type','size_bytes','purpose']),'Upload',201,security='worker')
operation(W+'/runs/{run_id}/artifacts/{artifact_id}/complete','post','completeWorkerUpload',obj({'attempt_token':UUID,'sha256':S(pattern='^[0-9a-f]{64}$')},['attempt_token','sha256']),'Artifact',202,security='worker')
from handoff_api import extend_contract
extend_contract(components,paths,operation,obj,arr,S,I,UUID,ref)
api={'openapi':'3.1.0','info':{'title':'Agent Platform core contract','version':'0.3.0','description':'Implementation specification; not a deployed service. Cross-resource authorization and state rules in API.md.'},'servers':[{'url':'https://platform.example.invalid'}],'paths':paths,'components':{'schemas':components,'securitySchemes':{'sessionCookie':{'type':'apiKey','in':'cookie','name':'platform_session'},'apiKey':{'type':'http','scheme':'bearer'},'workerMTLS':{'type':'mutualTLS'}}}}
save('contracts/openapi.json',api)

U1='11111111-1111-4111-8111-111111111111'
U2='22222222-2222-4222-8222-222222222222'
ANY={'type':'object'}
def n(id,kind,inputs=None,config=None,output=None): return {'id':id,'type':kind,'inputs':inputs or {},'config':config or {},'output_schema':output or ANY}
def b(ref): return {'ref':ref}
def edge(a,z,port=None): return dict(source=a,target=z,**({'port':port} if port else {}))
def graph(nodes,edges,ins=None,outs=None): return {'dsl_version':'1.0','input_schema':ins or ANY,'output_schema':outs or ANY,'nodes':nodes,'edges':edges,'policy':{'max_active_seconds':900,'max_child_depth':3,'max_child_calls':12,'budget_limit':'1.00','currency':'USD'}}
sales=graph([
 n('start','start'),
 n('stats','python',{'file':b('input#/file')},{'tool_version':U1},obj({'total':S(),'regions':dict(type='object',additionalProperties=S()),'order_count':I()},['total','regions','order_count'])),
 n('analysis','agent',{'stats':b('node:stats#')},{'model_profile_version':U2,'instructions':'依据已验证统计生成报告，不改变金额；不调用外部写工具。','tool_versions':[],'subapp_versions':[],'max_turns':12},obj({'report':S()},['report'])),
 n('end','end',{'report':b('node:analysis#/report'),'summary':b('node:stats#')})
],[edge('start','stats'),edge('stats','analysis'),edge('analysis','end')],obj({'file':obj({'artifact_id':UUID},['artifact_id'])},['file']),obj({'report':S(),'summary':ANY},['report','summary']))
save('examples/sales/workflow.json',sales)
save('examples/sales/layout.json',{'nodes':{id:{'x':i*280,'y':80} for i,id in enumerate(['start','stats','analysis','end'])},'viewport':{'x':0,'y':0,'zoom':1}})
sample=ROOT/'examples/sales/input.csv'
with sample.open('w',encoding='utf-8',newline='') as f:
    w=csv.writer(f);w.writerow(['order_id','date','region','amount']);w.writerows([['A1','2026-01-01','东区','120.50'],['A2','2026-01-02','东区','79.50'],['A3','2026-01-03','西区','50.00']])
from openpyxl import Workbook
wb=Workbook(); ws=wb.active; ws.title='sales'
with sample.open(encoding='utf-8',newline='') as f:
    for row in csv.reader(f): ws.append(row)
wb.save(ROOT/'examples/sales/input.xlsx')
save('examples/sales/expected.json',{'total':'250.00','regions':{'东区':'200.00','西区':'50.00'},'order_count':3})
branch=graph([n('start','start'),n('choose','condition',{'requires_approval':b('input#/requires_approval')},{'join_id':'join','cases':[{'input':'requires_approval','op':'eq','value':True,'port':'yes'}],'default_port':'no'}),n('approve','approval',{'proposal':{'literal':{'purpose':'仅示范人工业务确认，不授权任意后续写入'}}},{'title':'业务确认','expires_seconds':3600}),n('direct','model',{'task':{'literal':'输出无需人工确认的说明'}},{'model_profile_version':U2,'prompt_template':'{{task}}','response_mode':'json'}),n('join','join',config={'fork_id':'choose','mode':'all_active','required_sources':['approve','direct'],'optional_sources':[]}),n('end','end',{'results':b('node:join#/results')})],[edge('start','choose'),edge('choose','approve','yes'),edge('choose','direct','no'),edge('approve','join'),edge('direct','join'),edge('join','end')],obj({'requires_approval':{'type':'boolean'}},['requires_approval']))
save('examples/branch-approval/workflow.json',branch)
parallel=graph([n('start','start'),n('left','model',{'task':{'literal':'摘要'}},{'model_profile_version':U2,'prompt_template':'{{task}}','response_mode':'json'}),n('right','model',{'task':{'literal':'风险清单'}},{'model_profile_version':U2,'prompt_template':'{{task}}','response_mode':'json'}),n('join','join',config={'fork_id':'start','mode':'all_active','required_sources':['left','right'],'optional_sources':[]}),n('end','end',{'results':b('node:join#/results')})],[edge('start','left'),edge('start','right'),edge('left','join'),edge('right','join'),edge('join','end')])
save('examples/parallel/workflow.json',parallel)
bad=json.loads(json.dumps(sales));bad['edges'].append(edge('analysis','stats'))
save('examples/invalid/cycle.json',bad)
save('examples/event.json',{'schema_version':'1.0','event_id':U1,'run_id':U2,'seq':1,'revision':1,'type':'run.status','created_at':'2026-10-31T12:00:00Z','payload':{'state':'running'}})
manifest_schema=obj({'manifest_version':{'const':'1.0'},'application_id':UUID,'source_revision':I(minimum=1),'compiler_version':S(),'worker_image_digest':S(pattern='^sha256:[0-9a-f]{64}$'),'runtime_lock_sha256':S(pattern='^[0-9a-f]{64}$'),'state_schema_version':S(),'definition':dict(type='object'),'capability_card':components['DraftInput']['properties']['capability_card'],'tool_artifacts':arr(obj({'id':UUID,'image_digest':S(pattern='^sha256:[0-9a-f]{64}$')},['id','image_digest'])),'model_profile_versions':arr(UUID,uniqueItems=True),'subapp_versions':arr(UUID,uniqueItems=True)},['manifest_version','application_id','source_revision','compiler_version','worker_image_digest','runtime_lock_sha256','state_schema_version','definition','capability_card','tool_artifacts','model_profile_versions','subapp_versions'])
manifest_schema['properties']['connector_policies']=arr(obj({'version_id':UUID,'policy_sha256':S(pattern='^[0-9a-f]{64}$')},['version_id','policy_sha256']))
manifest_schema['required'].append('connector_policies')
manifest_schema['$schema']='https://json-schema.org/draft/2020-12/schema'
save('contracts/execution-manifest.schema.json',manifest_schema)
save('examples/execution-manifest.json',{'manifest_version':'1.0','application_id':'33333333-3333-4333-8333-333333333333','source_revision':1,'compiler_version':'fixture-compiler-1','worker_image_digest':'sha256:'+'0'*64,'runtime_lock_sha256':'0'*64,'state_schema_version':'fixture-state-1','definition':sales,'capability_card':{'purpose':'销售文件分析','includes':['CSV/XLSX统计'],'excludes':['生产系统写入'],'required_inputs':['file'],'expected_outputs':['report','summary'],'examples':['分析上传销售额','汇总各地区订单']},'tool_artifacts':[{'id':U1,'image_digest':'sha256:'+'0'*64}],'model_profile_versions':[U2],'subapp_versions':[],'connector_policies':[]})
save('contracts/versions.lock.example.json',{'status':'not_resolved','read_commits':{'vue_flow':'17953c329db2d5dc5f5097370cbf5e1173bab520','deepagents':'e21ce33d4486899069dda55254e6ec56e874a854','langgraph':'e5c961d7fbe7ca6fdd4c94c84d90511372514d55','opensandbox':'c2f55b20e945d89efe3d35220942a997a3551751','llamafactory':'9997aa078b4da35e04bb492a8a27d45141f4b80b'},'installed_packages':None,'worker_image_digest':None,'embedding_model_revision':None,'note':'Reading anchors only. Fill exact resolved distributions/digests after P0; do not claim these constitute a tested lock.'})
print(f'Generated {len(paths)} API paths, {sum(len(v) for v in paths.values())} operations, DSL/events and examples.')
