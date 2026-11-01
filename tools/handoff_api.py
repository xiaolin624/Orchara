"""v0.3 specification closure. Called by build_contracts, never starts a service."""
def extend_contract(components, paths, operation, obj, arr, S, I, UUID, ref):
    P='/api/v1'; W='/internal/v1'
    nullable_uuid={'type':['string','null'],'format':'uuid'}
    nullable_text={'type':['string','null']}
    dt=S(format='date-time')
    components.update({
      'IdentityView':obj({'principal_id':UUID,'kind':S(enum=['user','service']),'roles':arr(S()),'csrf_token':nullable_text,'expires_at':dt},['principal_id','kind','roles','csrf_token','expires_at']),
      'Invitation':obj({'principal_id':UUID,'activation_token':S(),'expires_at':dt},['principal_id','activation_token','expires_at']),
      'Conversation':obj({'id':UUID,'created_at':dt,'revision':I(minimum=1)},['id','created_at','revision']),
      'MessageView':obj({'id':S(),'role':S(enum=['user','assistant','system']),'request_id':UUID,'run_id':nullable_uuid,'body_artifact_id':nullable_uuid,'created_at':dt,'kind':S(enum=['request','interaction','result','status'])},['id','role','request_id','run_id','body_artifact_id','created_at','kind']),
      'ToolVersion':obj({'id':UUID,'state':S(enum=['submitted','building','ready','failed','revoked']),'approved':{'type':'boolean'},'image_digest':nullable_text,'manifest_sha256':S(),'input_schema':{'type':'object'},'output_schema':{'type':'object'}},['id','state','approved','image_digest','manifest_sha256','input_schema','output_schema']),
      'ModelProfile':obj({'id':UUID,'name':S(),'model_id':S(),'validation_state':S(enum=['pending','running','passed','failed']),'data_destination':S(),'evaluation_report_id':nullable_uuid},['id','name','model_id','validation_state','data_destination','evaluation_report_id']),
      'KnowledgeBase':obj({'id':UUID,'name':S(),'revision':I(minimum=1)},['id','name','revision']),
      'KnowledgeDocument':obj({'id':UUID,'knowledge_base_id':UUID,'state':S(enum=['uploaded','scanning','indexing','ready','failed','deleting','deleted']),'revision':I(minimum=1),'source_artifact_id':UUID},['id','knowledge_base_id','state','revision','source_artifact_id']),
      'EvaluationJob':obj({'id':UUID,'kind':S(enum=['application','model']),'state':S(enum=['queued','running','succeeded','failed','cancelled']),'background_job_id':UUID,'report_id':nullable_uuid,'error_code':nullable_text},['id','kind','state','background_job_id','report_id','error_code']),
      'BackgroundJob':obj({'id':UUID,'kind':S(),'state':S(enum=['ready','leased','succeeded','failed','cancelled']),'error_code':nullable_text},['id','kind','state','error_code']),
      'MemoryCandidate':obj({'id':UUID,'application_id':UUID,'text':S(),'state':S(enum=['pending','confirmed','rejected','expired']),'expires_at':dt},['id','application_id','text','state','expires_at']),
      'MemoryCandidateDecision':obj({'candidate_id':UUID,'state':S(enum=['confirmed','rejected']),'memory_id':nullable_uuid},['candidate_id','state','memory_id']),
      'BackgroundLease':obj({'job_id':UUID,'kind':S(),'target_id':UUID,'attempt_token':UUID,'lease_deadline':dt,'expected_revision':{'type':['integer','null']},'input_artifact_id':nullable_uuid,'input_url':nullable_text},['job_id','kind','target_id','attempt_token','lease_deadline','expected_revision','input_artifact_id','input_url']),
      'CapabilityResult':obj({'operation_id':UUID,'state':S(enum=['awaiting_approval','dispatching','succeeded','failed','unknown']),'receipt_artifact_id':nullable_uuid,'approval_id':nullable_uuid,'data':{}},['operation_id','state','receipt_artifact_id','approval_id']),
      'DeletionJob':obj({'id':UUID,'state':S(enum=['pending','running','completed','failed']),'remaining_scopes':arr(S())},['id','state','remaining_scopes'])
    })
    # Model registration precedes trusted compatibility evaluation.
    components['ModelInput']['properties'].pop('evaluation_report_id',None)
    components['ModelInput']['required'].remove('evaluation_report_id')
    components['ModelInput']['description']='Creates candidate profile. Server creates immutable manifest; validate before shared use. Endpoint/secret refs must be deployment-registered.'
    components['ModelConfig']=obj({'kind':S(enum=['generation','routing','embedding','judge']),'protocol':S(enum=['chat_compatible','embedding_compatible']),'context_window':I(minimum=1),'max_output_tokens':I(minimum=1),'timeout_ms':I(minimum=1),'embedding_dimension':I(minimum=1),'temperature':{'type':'number','minimum':0,'maximum':2},'fallback_profile_version_ids':arr(UUID,uniqueItems=True),'price':obj({'currency':S(minLength=3,maxLength=3),'input_per_million':S(),'output_per_million':S()},['currency','input_per_million','output_per_million'])},['kind','protocol','timeout_ms','price'])
    components['ModelConfig']['allOf']=[{'if':{'properties':{'kind':{'const':'embedding'}}},'then':{'required':['embedding_dimension']}},{'if':{'properties':{'kind':{'enum':['generation','routing','judge']}}},'then':{'required':['context_window','max_output_tokens']}}]
    components['ModelInput']['properties']['config']=ref('ModelConfig')
    components['ToolInput']['properties']['connector_version_ids']=arr(UUID,uniqueItems=True,default=[])
    components['CapabilityInput']['properties']['capability_kind']=S(enum=['model','tool','connector'])
    components['CapabilityInput']['required'].append('capability_kind')
    components['UploadInput']['properties']['purpose']['enum']+=['evaluation_dataset']
    components['Run']['properties']['phase']=S(enum=['idle','executing','waiting','finalizing'])
    components['Run']['required'].append('phase')
    components['RequestInput']['properties']['application_version_id']=UUID
    components['RequestInput']['properties']['handoff']=obj({'source_run_ids':arr(UUID),'artifact_ids':arr(UUID),'summary':S(maxLength=8000),'confirmed':{'const':True}},['source_run_ids','artifact_ids','summary','confirmed'])
    components['Request']['properties']['error_code']=nullable_text
    components['Request']['required'].append('error_code')
    components['InteractionReply']['anyOf']=[{'required':['message']},{'required':['candidate_version_id']},{'required':['artifact_ids']}]
    components['InteractionReply']['properties']['artifact_ids']['minItems']=1
    components['WorkerEvent']['properties']['type']=S(enum=['node.status'])
    components['WorkerEvent']['description']='Worker observations only. Run/approval/artifact/usage events are derived by Go controllers.'
    lease=components['Lease']
    lease['properties'].update({'job_kind':S(enum=['start','resume']),'execution_snapshot_id':UUID,'manifest_sha256':S(),'manifest_url':S(format='uri'),'input_artifact_id':UUID,'input_url':S(format='uri'),'resume_artifact_id':nullable_uuid,'resume_url':nullable_text})
    lease['required']+=['job_kind','execution_snapshot_id','manifest_sha256','manifest_url','input_artifact_id','input_url','resume_artifact_id','resume_url']
    lease['description']='Only supervisor can claim. Inline manifest is a projection; verify canonical bytes from manifest_url against manifest_sha256.'
    def page(name):return obj({'items':arr(ref(name)),'next_cursor':nullable_text},['items','next_cursor'])
    def replace_response(path,method,name,code=200):
        paths[path][method]['responses'][str(code)]['content']={'application/json':{'schema':ref(name)}}
    operation('/health/live','get','liveness',result=obj({'status':S(enum=['ok'])},['status']),security='none')
    operation('/health/ready','get','readiness',result=obj({'status':S(enum=['ready','not_ready'])},['status']),security='none')
    operation(P+'/auth/me','get','getIdentity',result='IdentityView')
    operation(P+'/auth/activate','post','activateUser',obj({'activation_token':S(),'password':S(minLength=12)},['activation_token','password']),result='Accepted',security='none',description='Consumes one-time activation token atomically. Token/password are sensitive; never log request body.')
    paths[P+'/auth/logout']['post']['security']=[{'sessionCookie':[]}]
    replace_response(P+'/admin/users','post','Invitation',201)
    operation(P+'/admin/principals/{principal_id}','patch','updatePrincipal',obj({'disabled':{'type':'boolean'},'reason':S(minLength=1)},['disabled','reason']),result='Accepted')
    operation(P+'/admin/users/{principal_id}/roles','put','setUserRoles',obj({'roles':arr(S(enum=['admin','creator','user']),minItems=1,uniqueItems=True)},['roles']),result='Accepted',description='Admin only; transaction must preserve at least one enabled human administrator.')
    operation(P+'/admin/credential-refs','get','listCredentialRefs',result=obj({'items':arr(obj({'id':S(),'description':S()},['id','description']))},['items']),description='Registered references only. Never returns secret values.')
    operation(P+'/admin/model-endpoints','get','listModelEndpoints',result=obj({'items':arr(obj({'id':S(),'data_destination':S()},['id','data_destination']))},['items']))
    operation(P+'/conversations','get','listConversations',result=page('Conversation'))
    operation(P+'/conversations/{conversation_id}','get','getConversation',result='Conversation')
    operation(P+'/conversations/{conversation_id}/messages','get','listMessages',result=page('MessageView'))
    operation(P+'/applications/{application_id}','get','getApplication',result='Application')
    operation(P+'/applications/{application_id}/versions','get','listVersions',result=page('Version'))
    components['PublicationDecisionResult']=obj({'id':UUID,'state':S(enum=['approved','rejected']),'version_id':nullable_uuid},['id','state','version_id'])
    replace_response(P+'/reviews/{review_id}/decision','post','PublicationDecisionResult')
    operation(P+'/reviews','get','listPublicationReviews',result=obj({'items':arr(obj({'id':UUID,'application_id':UUID,'state':S(),'manifest_sha256':S()},['id','application_id','state','manifest_sha256'])),'next_cursor':nullable_text},['items','next_cursor']))
    operation(P+'/reviews/{review_id}','get','getPublicationReview',result=obj({'id':UUID,'state':S(),'manifest_sha256':S(),'report_id':UUID,'snapshot':{'type':'object','additionalProperties':True}},['id','state','manifest_sha256','report_id','snapshot']))
    replace_response(P+'/applications/{application_id}/evaluations','post','EvaluationJob',202)
    operation(P+'/evaluations/{evaluation_id}','get','getEvaluation',result='EvaluationJob')
    operation(P+'/background-jobs/{job_id}','get','getBackgroundJob',result='BackgroundJob')
    operation(P+'/deletions/{deletion_id}','get','getDeletion',result='DeletionJob')
    components['DeletionAccepted']=obj({'id':UUID,'state':S(enum=['pending','running','completed','failed']),'resource_id':UUID},['id','state','resource_id'])
    for delete_path in [P+'/conversations/{conversation_id}',P+'/artifacts/{artifact_id}',P+'/memories/{memory_id}',P+'/knowledge-documents/{document_id}']:
        replace_response(delete_path,'delete','DeletionAccepted',202)
        paths[delete_path]['delete']['description']+=' Response id is deletion_job_id, not resource_id. Repeated deletion returns the same job.'
    operation(P+'/artifacts/{artifact_id}','get','getArtifact',result='Artifact')
    operation(P+'/artifacts/{artifact_id}/content','get','readArtifactContent',description='Authorized proxy download for immediate revocation. Range handling must not bypass ACL/tombstone.')
    paths[P+'/artifacts/{artifact_id}/content']['get']['responses']['200']={'description':'File bytes','content':{'application/octet-stream':{'schema':S(format='binary')}}}
    operation(P+'/runs/{run_id}/artifacts','get','listRunArtifacts',result=page('Artifact'))
    operation(P+'/tools','get','listTools',result=page('ToolVersion'))
    replace_response(P+'/tools/{tool_version_id}','get','ToolVersion')
    operation(P+'/models','get','listModels',result=page('ModelProfile'))
    operation(P+'/models/{model_version_id}','get','getModel',result='ModelProfile')
    operation(P+'/models/{model_version_id}/validate','post','validateModel',result='EvaluationJob',status=202,headers=['Idempotency-Key'])
    operation(P+'/knowledge-bases','get','listKnowledgeBases',result=page('KnowledgeBase'))
    operation(P+'/knowledge-bases/{knowledge_base_id}','get','getKnowledgeBase',result='KnowledgeBase')
    operation(P+'/knowledge-bases/{knowledge_base_id}/documents','get','listKnowledgeDocuments',result=page('KnowledgeDocument'))
    operation(P+'/knowledge-documents/{document_id}','get','getKnowledgeDocument',result='KnowledgeDocument')
    operation(P+'/memory-candidates','get','listMemoryCandidates',result=page('MemoryCandidate'),description='Human user only; personal scope from authenticated principal.')
    operation(P+'/memory-candidates/{candidate_id}/decision','post','decideMemoryCandidate',obj({'decision':S(enum=['confirm','reject'])},['decision']),'MemoryCandidateDecision',headers=['Idempotency-Key'])
    operation(P+'/memories/{memory_id}','get','getMemory',result='Memory')
    paths[P+'/memories']['get']['responses']['200']['content']['application/json']['schema']=page('Memory')
    operation(P+'/approvals','get','listApprovals',result=page('Approval'))
    operation(P+'/runs','get','listRuns',result=page('Run'))
    operation(W+'/runs/{run_id}/approvals','post','prepareApproval',obj({'attempt_token':UUID,'logical_key':S(),'operation_id':UUID,'proposal':{'type':'object','additionalProperties':True}},['attempt_token','logical_key','proposal']),'Approval',201,security='worker',description='Go derives approver and canonical hash. Replayed logical_key with changed params is a conflict.')
    replace_response(W+'/runs/{run_id}/capabilities','post','CapabilityResult')
    operation(W+'/runs/{run_id}/artifact-downloads','post','createWorkerDownload',obj({'attempt_token':UUID,'artifact_id':UUID},['attempt_token','artifact_id']),'Download',security='worker')
    operation(W+'/runs/{run_id}/artifact-status','post','getWorkerArtifactStatus',obj({'attempt_token':UUID,'artifact_id':UUID},['attempt_token','artifact_id']),'Artifact',security='worker')
    operation(W+'/runs/{run_id}/memory-candidates','post','proposeMemoryCandidate',obj({'attempt_token':UUID,'logical_key':S(),'text':S(minLength=1,maxLength=4000)},['attempt_token','logical_key','text']),'MemoryCandidate',201,security='worker')
    operation(W+'/background/jobs/claim','post','claimBackgroundJob',obj({'worker_id':S(),'supported_kinds':arr(S(),minItems=1)},['worker_id','supported_kinds']),'BackgroundLease',security='worker',description='Maintenance identity only. Never executes user-supplied handler names or commands.')
    paths[W+'/background/jobs/claim']['post']['responses']['204']={'description':'No compatible ready job'}
    operation(W+'/background/jobs/{job_id}/heartbeat','post','heartbeatBackgroundJob',obj({'attempt_token':UUID},['attempt_token']),'Accepted',security='worker')
    operation(W+'/background/jobs/{job_id}/result','post','completeBackgroundJob',obj({'attempt_token':UUID,'event_id':UUID,'state':S(enum=['succeeded','failed','cancelled']),'result_artifact_id':UUID,'error_code':S()},['attempt_token','event_id','state']),'BackgroundJob',security='worker')
    operation(W+'/background/jobs/{job_id}/model-calls','post','invokeBackgroundModel',obj({'attempt_token':UUID,'logical_key':S(),'model_profile_version_id':UUID,'params':{'type':'object','additionalProperties':True}},['attempt_token','logical_key','model_profile_version_id','params']),obj({'call_id':UUID,'state':S(),'result_artifact_id':nullable_uuid,'data':{}},['call_id','state','result_artifact_id']),security='worker',description='Maintenance identity and matching job lease; handler kind limits model purpose. Controlled model call goes to platform_model_calls ledger.')
    operation(W+'/background/jobs/{job_id}/artifact-uploads','post','createBackgroundUpload',obj({'attempt_token':UUID,'media_type':S(),'size_bytes':I(minimum=0,maximum=20971520),'purpose':S(enum=['report','log','receipt','parsed_data'])},['attempt_token','media_type','size_bytes','purpose']),'Upload',201,security='worker')
    operation(W+'/background/jobs/{job_id}/artifacts/{artifact_id}/complete','post','completeBackgroundUpload',obj({'attempt_token':UUID,'sha256':S(pattern='^[0-9a-f]{64}$')},['attempt_token','sha256']),'Artifact',202,security='worker')
    operation(W+'/background/jobs/{job_id}/artifact-downloads','post','createBackgroundDownload',obj({'attempt_token':UUID,'artifact_id':UUID},['attempt_token','artifact_id']),'Download',security='worker')
    operation(W+'/background/jobs/{job_id}/artifact-status','post','getBackgroundArtifactStatus',obj({'attempt_token':UUID,'artifact_id':UUID},['attempt_token','artifact_id']),'Artifact',security='worker')
    operation(W+'/runs/{run_id}/model-stream','post','streamModelCall','CapabilityInput',security='worker',description='Controlled model stream; each generation has operation/attempt identity. See API.md stream envelope and reset rules.')
    paths[W+'/runs/{run_id}/model-stream']['post']['responses']['200']={'description':'Scoped model SSE','content':{'text/event-stream':{'schema':S()}}}
    # Enforce the documented pagination/ETag/error surface in the machine contract.
    for path,methods in paths.items():
        for method,op in methods.items():
            if method=='get' and op['operationId'].startswith('list') and op['operationId'] not in {'listCredentialRefs','listModelEndpoints'}:
                op['parameters'] += [{'name':'cursor','in':'query','required':False,'schema':S()},{'name':'limit','in':'query','required':False,'schema':I(minimum=1,maximum=100,default=20)}]
            if method=='get' and op['operationId'] in {'listModels','listTools','listMemories','listMemoryCandidates'}:
                op['parameters'].append({'name':'application_id','in':'query','required':False,'schema':UUID})
            for code in [413]:
                op['responses'][str(code)]={'description':'Payload/resource limit','content':{'application/json':{'schema':ref('Error')}}}
            if path.startswith(W):
                op['x-internal-role']='supervisor' if op['operationId'] in {'claimJob','confirmAttemptExit'} else ('maintenance' if '/background/' in path else 'worker')
    for path,method in [(P+'/applications/{application_id}/draft','get'),(P+'/applications/{application_id}/draft','put'),(P+'/memories/{memory_id}','get'),(P+'/memories/{memory_id}','patch')]:
        paths[path][method]['responses']['200']['headers']={'ETag':{'required':True,'schema':S(),'description':'Opaque revision token for If-Match'}}
