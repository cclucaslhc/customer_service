"""Real scoped pgvector and synthetic model Worker integration; no online models."""
from datetime import datetime,timezone
import hashlib
import uuid
import pytest
import sqlalchemy as sa
from app.knowledge import KnowledgeService,TOKENIZER_VERSION,tokenize
from app.knowledge_models import CHUNKER_VERSION
from app.knowledge_retrieval import KnowledgeIndexChanged
from app.persistence.schema import metadata
from app.persistence.durable_chat_repository import DurableChatRepository
from app.rag_rollout_service import RagRolloutService
from app.rag_routing import binding_for_runtime
from test_durable_chat import submit
from test_rag_rollout_service import planned,release,queue_setup,postgres_database
from test_rag_release_service import approve_both
from test_rag_rollout_plan import plan

CONTENT='退货需要订单号。'
VECTOR=[1.0]+[0.0]*1023


class Embedding:
    max_length=8192
    def encode(self,texts,*,query=False):
        return [VECTOR[:] for _ in texts]


@pytest.fixture
def vector_rollout(planned,request):
    bundle,service,args,tokens=planned
    indexes=metadata.tables['knowledge_index_versions']
    releases=metadata.tables['rag_release_intents']
    with bundle.engine.connect() as db:
        previous=db.execute(sa.select(indexes).where(indexes.c.status=='active')).mappings().one()
        candidate=db.execute(sa.select(indexes).where(indexes.c.index_id==uuid.UUID(args['current_binding']['index_id']))).mappings().one()
        definition=db.execute(sa.select(releases.c.body).where(releases.c.release_id==uuid.UUID(args['release_id']))).scalar_one()
    tenant=uuid.UUID(args['tenant_id'])
    actor=uuid.UUID(bundle.identity.resolve(tokens[0]).user_id)
    doc=uuid.uuid4()
    now=datetime.now(timezone.utc)
    digest=hashlib.sha256(CONTENT.encode()).hexdigest()
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['documents'].insert().values(id=doc,tenant_id=tenant,
            display_name='Synthetic return policy',stored_name='synthetic-route.txt',extension='.txt',
            size=len(CONTENT.encode()),sha256=digest,status='ready',chunk_count=1,version=1,created_at=now,created_by=actor))
        db.execute(metadata.tables['knowledge_chunks'].insert().values(tenant_id=tenant,document_id=doc,
            chunk_index=0,content=CONTENT,tokenized=tokenize(CONTENT),block_type='paragraph'))
        db.execute(metadata.tables['knowledge_chunk_versions'].insert().values(tenant_id=tenant,document_id=doc,
            document_version=1,chunk_index=0,content_sha256=digest,content=CONTENT,block_type='paragraph',
            metadata_json={},parser_version='synthetic',chunker_version=CHUNKER_VERSION,created_at=now))
    repo=bundle.knowledge.authenticated(tokens[0])
    new=[]
    for old in (previous,candidate):
        repo.store_embedding(profile_id=old['profile_id'],document_id=doc,document_version=1,
            chunk_index=0,content_sha256=digest,vector=VECTOR)
        index=uuid.uuid4()
        service.admin.stage_existing(tenant_id=tenant,profile_id=old['profile_id'],index_id=index,
            lexical_version=TOKENIZER_VERSION,chunker_version=CHUNKER_VERSION)
        manifest=service.admin.seal(tenant_id=tenant,index_id=index)['manifest_sha256']
        new.append((index,manifest))
    service.admin.activate(tenant_id=tenant,index_id=new[0][0],expected_active_index_id=previous['index_id'])
    instructions=getattr(request,'param',{}).get('instructions','Answer the synthetic question.')
    binding={**args['current_binding'],'index_id':str(new[1][0]),'manifest_sha256':new[1][1],
        **binding_for_runtime('deepseek','synthetic',instructions)}
    evidence={**definition['evidence'],**binding,'completed_at':now.isoformat()}
    receipt=service.create(release_id=uuid.uuid4(),previous_index_id=new[0][0],policy=definition['policy'],
        evidence=evidence,binding=binding,lifetime_seconds=getattr(request,'param',{}).get('lifetime_seconds',600),rollout_plan=plan())
    args={**args,'release_id':receipt['release_id'],'expected_version':0,
        'expected_body_sha256':receipt['body_sha256'],'current_binding':binding}
    approve_both(service,args,tokens)
    controller=RagRolloutService(bundle.engine)
    staged=controller.stage(rollout_id=uuid.uuid4(),**args)
    started=controller.start(tenant_id=str(tenant),rollout_id=staged['rollout_id'],expected_rollout_version=0,
        expected_plan_sha256=staged['plan_sha256'],expected_release_version=args['expected_version'],
        expected_body_sha256=args['expected_body_sha256'],current_binding=binding)
    control=dict(tenant_id=str(tenant),rollout_id=staged['rollout_id'],expected_rollout_version=started['version'],
        expected_plan_sha256=staged['plan_sha256'])
    return bundle,controller,control,tokens,doc,candidate['profile_id'],str(new[1][0])


def candidate_session(control):
    for number in range(1,100001):
        sid=str(uuid.UUID(int=number))
        encoded=f"{control['tenant_id']}:{sid}:{control['rollout_id']}:{plan()['cohort_salt']}"
        if int(hashlib.md5(encoded.encode()).hexdigest()[:8],16)%10000<100:
            return sid
    raise AssertionError('Synthetic candidate cohort not found')


def test_only_current_route_reads_ready_profile_and_pause_revokes_it(vector_rollout,tmp_path):
    bundle,controller,control,tokens,doc,profile,index=vector_rollout
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(control))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    route=owner.pin_rag_route(run_id=rid,session_id=sid)
    assert route['index_id']==index
    repo=bundle.knowledge.authenticated(tokens[0])
    assert repo.search_vector_candidates(VECTOR,profile_id=profile,limit=3)==[]
    routed=repo.with_route(route)
    assert routed.active_vector_profile()['index_id']==index
    assert routed.search_vector_candidates(VECTOR,profile_id=profile,limit=3)[0]['document_id']==str(doc)
    with pytest.raises(KnowledgeIndexChanged):
        repo.with_route({**route,'route_version':999}).active_vector_profile()
    service=KnowledgeService(repository=repo,embedding_gateway=Embedding(),knowledge_dir=tmp_path/'knowledge',
        asset_dir=tmp_path/'assets',legacy_upload_dir=tmp_path/'legacy')
    scoped=service.for_route(route)
    assert scoped.active_index_id()==index and service.active_index_id()!=index
    assert scoped.search('退货订单号',index_id=index,pinned=True)[0]['content']==CONTENT
    controller.pause(**control)
    for operation in (routed.active_vector_profile,
                      lambda:routed.search_vector_candidates(VECTOR,profile_id=profile,limit=3)):
        with pytest.raises(KnowledgeIndexChanged):
            operation()


@pytest.mark.asyncio
@pytest.mark.parametrize('pause_before_answer',[False,True])
async def test_real_worker_pins_retrieves_and_fences_paused_answer(vector_rollout,tmp_path,pause_before_answer):
    from pydantic_ai.models.function import FunctionModel,DeltaToolCall
    from pydantic_ai.messages import ToolReturnPart
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    bundle,controller,control,tokens,doc,profile,index=vector_rollout
    root=DurableChatRepository(bundle.engine)
    customer=root.authenticated(tokens[0])
    sid,rid=submit(customer,sid=candidate_session(control),content='退货需要什么？')
    async def stream(messages,info):
        if any(isinstance(part,ToolReturnPart) for message in messages for part in message.parts):
            if pause_before_answer:
                controller.pause(**control)
            yield CONTENT
        else:
            yield {0:DeltaToolCall(name='search_knowledge',json_args='{"query":"退货订单号"}',tool_call_id='synthetic-route-search')}
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
        def build_model(self,snapshot):
            return FunctionModel(stream_function=stream)
    worker=ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential,job:Gateway(),rag_embedding_gateway=Embedding())
    assert await worker.run_once()
    with bundle.engine.connect() as db:
        run=db.execute(sa.select(metadata.tables['agent_runs']).where(metadata.tables['agent_runs'].c.id==uuid.UUID(rid))).mappings().one()
        assert run['rag_route']['index_id']==index and run['rag_route']['rollout_id']==control['rollout_id']
        assert run['status']==('interrupted' if pause_before_answer else 'complete')
    history=customer.history(sid)
    assert len(history)==(1 if pause_before_answer else 2)
    events=customer.read_events(sid,rid)['events']
    if pause_before_answer:
        assert not any(event['event']=='token' and CONTENT in str(event['data']) for event in events)
    else:
        assert history[-1]['content']==CONTENT
        assert any(event['event']=='token' and event['data'].get('text')==CONTENT for event in events)
    assert not (tmp_path/'unused.key').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['model','prompt'])
async def test_worker_rejects_unapproved_runtime_context_before_building_model(vector_rollout,tmp_path,changed):
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    bundle,_,control,tokens,*_=vector_rollout
    root=DurableChatRepository(bundle.engine)
    sid,rid=uuid.uuid4(),uuid.uuid4()
    root.authenticated(tokens[0]).enqueue(session_id=str(sid),request_id=str(rid),content='退货需要什么？',
        title='Synthetic binding mismatch',provider='deepseek',model='unapproved-model' if changed=='model' else 'synthetic',prompt_version_id=None,
        instructions='Unapproved instructions' if changed=='prompt' else 'Answer the synthetic question.')
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','unapproved-model' if changed=='model' else 'synthetic','unused')
        def build_model(self,snapshot):
            raise AssertionError('Must reject context before building any model')
    worker=ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential,job:Gateway(),rag_embedding_gateway=Embedding())
    assert await worker.run_once()
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['agent_runs']).where(metadata.tables['agent_runs'].c.id==rid)).mappings().one()
        assert row['status']=='interrupted' and row['error_code']=='knowledge_index_changed'
        assert row['rag_route'] is None


@pytest.mark.asyncio
async def test_gray_worker_transport_failure_does_not_attempt_other_provider(vector_rollout,tmp_path):
    import httpx
    from pydantic_ai.models.function import FunctionModel
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    bundle,_,control,tokens,*_=vector_rollout
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(control))
    fallbacks=[]
    async def stream(messages,info):
        if False:
            yield ''
        raise httpx.ConnectError('Synthetic provider offline')
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
        def build_model(self,snapshot):
            return FunctionModel(stream_function=stream)
        def fallback_snapshot(self,provider):
            fallbacks.append(provider)
            raise AssertionError('Gray release did not approve any alternate model')
    worker=ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential,job:Gateway(),rag_embedding_gateway=Embedding())
    assert await worker.run_once()
    assert not fallbacks
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['agent_runs']).where(metadata.tables['agent_runs'].c.id==uuid.UUID(rid))).mappings().one()
        assert row['rag_route']['rollout_id']==control['rollout_id'] and row['status']=='error'
