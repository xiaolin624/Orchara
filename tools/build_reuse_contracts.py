"""Generate the component inventory and evaluation documentation fixtures."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def save(path,data):
    target=ROOT/path;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

records=[]
def component(id,repo,role,status,phase,mode,group=None,commit=None,evidence=None):
    records.append(dict(id=id,repository='https://github.com/'+repo,role=role,status=status,phase=phase,integration=mode,clone_group=group,read_commit=commit,evidence=evidence,evidence_level='pinned_source' if commit else 'repository_or_existing_plan_only'))

P='research/platform-reuse-audit.md'
R='research/rag-memory-reuse-audit.md'
E='research/evaluation-reuse-audit.md'
F='research/infrastructure-reuse-audit.md'
component('deepagents','langchain-ai/deepagents','自主Agent节点','confirmed_base','week1','library','core','e21ce33d4486899069dda55254e6ec56e874a854','ARCHITECTURE.md')
component('langgraph','langchain-ai/langgraph','整图执行与Postgres checkpoint','confirmed_base','week1','library','core','e5c961d7fbe7ca6fdd4c94c84d90511372514d55','research/workflow-persistence-and-resume.md')
component('vue-flow','bcakmakoglu/vue-flow','画布渲染交互','confirmed_base','week1','library','core','17953c329db2d5dc5f5097370cbf5e1173bab520',P)
component('opensandbox','opensandbox-group/OpenSandbox','脚本生命周期与执行API','confirmed_base','week1','service','core','c2f55b20e945d89efe3d35220942a997a3551751','research/script-execution-options.md')
component('gvisor','google/gvisor','Linux脚本隔离运行时','confirmed_base','week1','runtime',None,None,'research/script-execution-options.md')
for id,repo,role in [('vue','vuejs/core','Vue前端'),('vite','vitejs/vite','前端构建'),('typescript','microsoft/TypeScript','前端类型'),('chi','go-chi/chi','Go路由'),('pgx','jackc/pgx','Go PostgreSQL访问'),('sqlc','sqlc-dev/sqlc','SQL类型代码生成'),('postgres','postgres/postgres','业务状态与事件数据库'),('pgvector','pgvector/pgvector','向量扩展'),('redis','redis/redis','可重建缓存/限流/唤醒')]:
    component(id,repo,role,'recommended_default','week1','dependency_or_service',evidence='ARCHITECTURE.md')
component('element-plus','element-plus/element-plus','表单/表格/弹窗/上传控件','recommended_default','week1','library',None,'a8d102a91b96f52c433d0d3ef7c91c565a9aa317',P)
component('coze','coze-dev/coze-studio','Go平台/画布转换参考','source_reference','research','reference','research','fefb05ff27be1da939612fbf9faf5db62583b8ae',P)
component('dify','langgenius/dify','变量池/运行准备/恢复测试参考','source_reference','research','reference','research','4c521a3c0a1f60607c8a7bcf90792f34adcf18d0',P)
component('flowise','FlowiseAI/Flowise','导出/变量选择/分支测试参考','source_reference','research','reference','research','9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb',P)
component('mem0','mem0ai/mem0','长期记忆对照后端','gated_candidate','v1','adapter','research','753496cf1435d76c3a711f3af51303f7fdcb6420',R)
component('llamaindex','run-llama/llama_index','优先复用分块/来源转换','gated_candidate','v1','library_adapter','research','619d74664e8404612038bc15a1a40c686037277c',R)
component('docling','docling-project/docling','普通PDF解析/出处候选','gated_candidate','v1','isolated_parser','research','764b5d4dc22b03ea87afd4c2207f510300b27a5b',R)
component('litellm','BerriAI/litellm','多供应商适配候选','gated_candidate','later','adapter_or_service',None,'8ff495be9624adbb45fd0e4c829d22f401ddab75',F)
component('mcp-python','modelcontextprotocol/python-sdk','后续工具协议连接器','gated_candidate','later','library_adapter',None,'d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14',F)
component('seaweedfs','seaweedfs/seaweedfs','自部署S3候选','gated_candidate','v1','service','research','f5989a21fb7c550a0a014a52bd664b10ecc3850d',F)
for id,repo,role in [('otel-go','open-telemetry/opentelemetry-go','Go追踪/指标'),('otel-python','open-telemetry/opentelemetry-python','Python追踪/指标')]:
    component(id,repo,role,'recommended_default','v1','library',evidence='ARCHITECTURE.md')
component('otel-collector','open-telemetry/opentelemetry-collector-contrib','本地观测处理/防御性过滤','gated_candidate','v1','optional_service',None,'504480882aa709e0a665bfff10a1263505c89fc1',F)
component('pytest','pytest-dev/pytest','Python程序测试','development_only','week1','test_dependency',evidence='TESTING-AND-DATASETS.md')
component('deepeval','confident-ai/deepeval','首个Agent质量适配器候选','gated_candidate','v1','development_adapter','evaluation','90e230cbe916c73240a48312c494065a39bf52d1',E)
component('promptfoo','promptfoo/promptfoo','HTTP黑盒质量替代候选','gated_candidate','later','development_adapter','evaluation','5582f347bbc5359b201ce2d48c9c7fbebd8dcbd9',E)
component('ragas','vibrantlabsai/ragas','RAG专项质量候选','gated_candidate','v1','development_adapter','evaluation','298b68274234c060deacab3cf5fb52aa3a20e885',E)
component('tau2','sierra-research/tau2-bench','环境/动作/政策任务子集','development_only','v1','benchmark_adapter','evaluation','da0835f6c67baef58bdda18f2a3b037dfb3a1754',E)
component('agentdojo','ethz-spylab/agentdojo','工具结果注入/额外副作用任务','development_only','v1','benchmark_adapter','evaluation','089ed468cf3ed0322acc66b0211f26d9d90dbf60',E)
component('toxiproxy','Shopify/toxiproxy','网络时序故障注入','development_only','v1','test_service','evaluation','49299d8838525126e09c85ebe0ba89940a3261e5',F)
component('playwright','microsoft/playwright','浏览器端到端测试','development_only','week1','test_tool',None,'deb7e86201e26cb4a8e25191fce5ef2f7a28aa87',F)
component('k6','grafana/k6','独立负载测试','development_only','v1','test_tool',None,'006583cf0feebccbb106a05ef3ea705759b4be00',F)
component('llamafactory','hiyouga/LlamaFactory','离线LoRA/SFT实验','confirmed_base','training','experiment','training','9997aa078b4da35e04bb492a8a27d45141f4b80b','TRAINING-EXPERIMENT.md')
save('contracts/components.json',dict(schema_version='1.0',queried_at='2026-10-31',status='accepted_reuse_strategy',confirmed_decisions=['Q45','Q46'],runtime_validation='not_run',note='The user confirmed the reuse strategy. Gated candidates remain gated; reading commits are NOT a tested install lock. Group membership never means all candidates must be deployed. License details and unreviewed scopes are recorded in evidence files.',components=records))

def obj(props,required):return dict(type='object',properties=props,required=required,additionalProperties=False)
STR={'type':'string'}
assertions={
 'json_equals':obj({'kind':{'const':'json_equals'},'pointer':STR,'expected':{}},['kind','pointer','expected']),
 'decimal_equals':obj({'kind':{'const':'decimal_equals'},'pointer':STR,'expected':{'type':'string','pattern':r'^-?\d+(\.\d+)?$'}},['kind','pointer','expected']),
 'run_state':obj({'kind':{'const':'run_state'},'expected':{'enum':['succeeded','failed','cancelled','needs_reconciliation','recovery_pending']}},['kind','expected']),
 'effect_count':obj({'kind':{'const':'effect_count'},'capability':STR,'expected':{'type':'integer','minimum':0},'source':{'const':'stub_ledger'}},['kind','capability','expected','source']),
 'citation_sources':obj({'kind':{'const':'citation_sources'},'allowed_source_ids':{'type':'array','items':STR,'minItems':1},'require_at_least_one':{'type':'boolean'}},['kind','allowed_source_ids','require_at_least_one'])
}
case=obj({'schema_version':{'const':'1.0'},'case_id':{'type':'string','minLength':1},'category':{'enum':['sales','routing','rag','memory','delegation','approval_effects','lifecycle']},'family_id':STR,'split':{'enum':['dev','frozen_test']},'target':obj({'kind':{'enum':['published_version','debug_snapshot']},'id':STR},['kind','id']),'input':{'type':'object','additionalProperties':True},'fixture_id':STR,'assertions':{'type':'array','minItems':1,'items':{'oneOf':list(assertions.values())}},'provenance':obj({'origin':{'enum':['self_created','adapted_external']},'reference':STR,'license_review':{'enum':['not_applicable','pending','reviewed']}},['origin','reference','license_review'])},['schema_version','case_id','category','family_id','split','target','input','fixture_id','assertions','provenance'])
case.update({'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:agent-platform:evaluation-case:1.0'})
save('contracts/evaluation-case.schema.json',case)
sales=dict(schema_version='1.0',case_id='sales-basic-001',category='sales',family_id='sales-three-orders',split='dev',target={'kind':'published_version','id':'fixture-sales-v1'},input={'file_fixture':'examples/sales/input.csv'},fixture_id='sales-three-orders-v1',assertions=[{'kind':'run_state','expected':'succeeded'},{'kind':'decimal_equals','pointer':'/summary/total','expected':'250.00'},{'kind':'json_equals','pointer':'/summary/order_count','expected':3}],provenance={'origin':'self_created','reference':'examples/sales/input.csv','license_review':'not_applicable'})
save('examples/evaluation/sales-case.json',sales)
unknown=dict(schema_version='1.0',case_id='write-receipt-lost-001',category='approval_effects',family_id='write-receipt-loss',split='frozen_test',target={'kind':'debug_snapshot','id':'fixture-approved-write-v1'},input={'to':'test-inbox','body':'synthetic payload'},fixture_id='stub-write-then-drop-receipt-v1',assertions=[{'kind':'run_state','expected':'needs_reconciliation'},{'kind':'effect_count','capability':'test.send','expected':1,'source':'stub_ledger'}],provenance={'origin':'self_created','reference':'EVALUATION.md FAULT-03','license_review':'not_applicable'})
save('examples/evaluation/unknown-write-case.json',unknown)
save('examples/evaluation/invalid-empty-checker.json',{**sales,'case_id':'invalid-empty','assertions':[]})
print('Generated component inventory and evaluation case contracts; no component installation or task execution.')
