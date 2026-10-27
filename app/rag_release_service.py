"""Owner-operated dual-identity approval and atomic publication. No HTTP API.

Current model/Prompt/dataset bindings must come from a trusted controller, not
an untrusted request echo. Readiness is not a check-then-activate permission:
an eventual executor must repeat all checks within the activation transaction.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta
import sqlalchemy as sa
from app.identity import AuthenticationError, AuthorizationError, DelegatedSession, token_hash
from app.persistence.identity_repository import IdentityRepository
from app.persistence.schema import metadata
from app.rag_index_admin import RagIndexAdmin
from app.rag_release_gate import assess_quality
from app.rag_rollout_plan import validate_plan


def _freeze(body):
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(encoded)>65536:
        raise ValueError('Release definition exceeds storage bound')
    return json.loads(encoded),hashlib.sha256(encoded).hexdigest()


class RagReleaseService:
    def __init__(self,engine):
        self.engine=engine
        self.admin=RagIndexAdmin(engine)
        self.identity=IdentityRepository(engine)
        self.table=metadata.tables['rag_release_intents']

    def _guard(self,db):
        self.admin._guard(db)
        if not db.execute(sa.text("SELECT pg_get_userbyid(relowner)=current_user FROM pg_class "
            "WHERE oid='public.rag_release_intents'::regclass")).scalar_one():
            raise PermissionError('Release approval requires the control-table owner')

    def _principal(self,db,credential):
        principal=self.identity.resolve(credential,connection=db)
        # NO KEY UPDATE blocks revocation without blocking FK key-share locks
        # used by identity events. Keep tenant -> user -> membership -> session
        # order so membership/password revocation can complete before our locks.
        for name,predicate in (
            ('identity_tenants',lambda t:t.c.id==uuid.UUID(principal.tenant_id)),
            ('identity_users',lambda t:t.c.id==uuid.UUID(principal.user_id)),
            ('identity_memberships',lambda t:sa.and_(t.c.tenant_id==uuid.UUID(principal.tenant_id),
                                                   t.c.user_id==uuid.UUID(principal.user_id))),
            ('identity_sessions',lambda t:t.c.token_hash==token_hash(credential))):
            table=metadata.tables[name]
            if db.execute(sa.select(table).where(predicate(table)).with_for_update(key_share=True)).first() is None:
                raise AuthenticationError()
        principal=self.identity.resolve(credential,connection=db)
        principal.require('config.manage')
        sessions=metadata.tables['identity_sessions']
        if not db.execute(sa.select(sessions.c.token_hash).where(
                sessions.c.token_hash==token_hash(credential),sessions.c.expires_at>sa.func.clock_timestamp())).first():
            raise AuthenticationError()
        return principal

    def _index_context(self,db,binding,previous):
        tenant=uuid.UUID(binding['tenant_id'])
        candidate=uuid.UUID(binding['index_id'])
        indexes=metadata.tables['knowledge_index_versions']
        current=db.execute(sa.select(indexes.c.index_id).where(indexes.c.tenant_id==tenant,
            indexes.c.status=='active')).scalar_one_or_none()
        if current!=previous:
            raise ValueError('Release baseline changed')
        row=db.execute(sa.select(indexes).where(indexes.c.tenant_id==tenant,
            indexes.c.index_id==candidate,indexes.c.status=='ready')).mappings().first()
        if not row or row['manifest_sha256']!=binding['manifest_sha256']:
            raise ValueError('Release candidate changed or unavailable')
        manifest,_=self.admin._manifest(db,row)
        if manifest!=binding['manifest_sha256']:
            raise ValueError('Release manifest is no longer current')

    def create(self,*,release_id,previous_index_id,policy,evidence,binding,lifetime_seconds,rollout_plan=None):
        if type(lifetime_seconds) is not int or not 1<=lifetime_seconds<=86400:
            raise ValueError('Explicit release lifetime must be 1-86400 seconds')
        definition={'policy':policy,'evidence':evidence,'binding':binding}
        if rollout_plan is not None:
            definition['rollout_plan']=validate_plan(rollout_plan)
        body,digest=_freeze(definition)
        with self.engine.begin() as db:
            self._guard(db)
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            result=assess_quality(body['policy'],body['evidence'],body['binding'],now=now)
            if not result['thresholds_satisfied']:
                raise ValueError('Release quality thresholds are not satisfied')
            refs=body['evidence']['reviewer_refs']
            if len(refs)!=2:
                raise ValueError('Release requires exactly two reviewer identities')
            tenant=uuid.UUID(body['binding']['tenant_id'])
            previous=uuid.UUID(str(previous_index_id))
            self._index_context(db,body['binding'],previous)
            members,users,tenants=(metadata.tables[name] for name in
                ('identity_memberships','identity_users','identity_tenants'))
            for ref in refs:
                valid=db.execute(sa.select(members.c.user_id).join(users,users.c.id==members.c.user_id)
                    .join(tenants,tenants.c.id==members.c.tenant_id).where(members.c.tenant_id==tenant,
                        members.c.user_id==uuid.UUID(ref),members.c.role=='admin',members.c.active.is_(True),
                        users.c.active.is_(True),tenants.c.active.is_(True))).first()
                if not valid:
                    raise AuthorizationError('Reviewer is not an active tenant administrator')
            completed=datetime.fromisoformat(body['evidence']['completed_at'].replace('Z','+00:00'))
            expires=min(now+timedelta(seconds=lifetime_seconds),
                        completed+timedelta(seconds=body['policy']['max_age_seconds']))
            rid=uuid.UUID(str(release_id))
            db.execute(self.table.insert().values(tenant_id=tenant,release_id=rid,
                previous_index_id=previous,candidate_index_id=uuid.UUID(body['binding']['index_id']),
                reviewer_a=uuid.UUID(refs[0]),reviewer_b=uuid.UUID(refs[1]),body=body,body_sha256=digest,
                created_at=now,expires_at=expires))
            self.admin._audit(db,tenant,rid,'release_create',{'release_id':str(rid),'body_sha256':digest})
            return {'release_id':str(rid),'tenant_id':str(tenant),'version':0,'state':'pending',
                    'body_sha256':digest,'expires_at':expires.isoformat()}

    def _locked(self,db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding):
        if type(expected_version) is not int or expected_version<0:
            raise ValueError('An explicit release version is required')
        row=db.execute(sa.select(self.table).where(self.table.c.tenant_id==uuid.UUID(str(tenant_id)),
            self.table.c.release_id==uuid.UUID(str(release_id))).with_for_update()).mappings().first()
        if not row or row['state'] not in ('pending','approved'):
            raise ValueError('Release is unavailable or requires fresh review')
        if row['version']!=expected_version or row['body_sha256']!=expected_body_sha256:
            raise ValueError('Release receipt changed')
        body,digest=_freeze(row['body'])
        if (set(body) not in ({'policy','evidence','binding'}, {'policy','evidence','binding','rollout_plan'})
                or digest!=row['body_sha256']):
            raise ValueError('Release definition failed integrity verification')
        if 'rollout_plan' in body:
            validate_plan(body['rollout_plan'])
        now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if row['expires_at']<=now:
            raise ValueError('Release approval window expired')
        assessment=assess_quality(body['policy'],body['evidence'],current_binding,now=now)
        if (not assessment['thresholds_satisfied'] or body['binding']!=current_binding
                or current_binding['tenant_id']!=str(row['tenant_id'])
                or current_binding['index_id']!=str(row['candidate_index_id'])
                or body['evidence']['reviewer_refs']!=[str(row['reviewer_a']),str(row['reviewer_b'])]):
            raise ValueError('Release evidence or current binding changed')
        self._index_context(db,current_binding,row['previous_index_id'])
        return dict(row)

    def _live(self,db,row,slot):
        digest=row['approval_'+slot+'_session_hash']
        if digest is None:
            return False
        try:
            principal=self._principal(db,DelegatedSession(digest))
            return principal.tenant_id==str(row['tenant_id']) and principal.user_id==str(row['reviewer_'+slot])
        except (AuthenticationError,AuthorizationError):
            return False

    @staticmethod
    def _summary(row,live):
        return {'release_id':str(row['release_id']),'tenant_id':str(row['tenant_id']),
            'version':row['version'],'state':row['state'],'body_sha256':row['body_sha256'],
            'valid_approvals':sum(live),'scope':('atomic_index_publication' if row['state']=='executed'
                else 'approval_readiness_not_index_execution')}

    def approve(self,*,tenant_id,release_id,expected_version,expected_body_sha256,current_binding,token):
        if not isinstance(token,str):
            raise AuthenticationError()
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding)
            principal=self._principal(db,token)
            if principal.tenant_id!=str(row['tenant_id']):
                raise AuthorizationError('Approval tenant mismatch')
            slot=next((key for key in ('a','b') if str(row['reviewer_'+key])==principal.user_id),None)
            if slot is None:
                raise AuthorizationError('Identity is not a designated reviewer')
            field='approval_'+slot+'_session_hash'
            changed=row[field]!=token_hash(token)
            row[field]=token_hash(token)
            if changed:
                row['approval_'+slot+'_at']=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            live=[self._live(db,row,key) for key in ('a','b')]
            state='approved' if all(live) else 'pending'
            if changed or state!=row['state']:
                row['state'],row['version']=state,row['version']+1
                db.execute(self.table.update().where(self.table.c.tenant_id==row['tenant_id'],
                    self.table.c.release_id==row['release_id']).values(**{key:row[key] for key in
                        (field,'approval_'+slot+'_at','state','version')}))
                self.admin._audit(db,row['tenant_id'],row['release_id'],'release_approve',{
                    'release_id':str(row['release_id']),'reviewer_user_id':principal.user_id,
                    'version':row['version'],'body_sha256':row['body_sha256']})
            return self._summary(row,live)

    def check_ready(self,*,tenant_id,release_id,expected_version,expected_body_sha256,current_binding):
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding)
            live=[self._live(db,row,key) for key in ('a','b')]
            if row['state']!='approved' or not all(live):
                raise AuthorizationError('Two current administrator approvals are required')
            return self._summary(row,live)

    def execute(self,*,tenant_id,release_id,expected_version,expected_body_sha256,current_binding):
        """Owner-only one-shot publication, not gray rollout or automatic recovery.

        The trusted controller supplies current bindings. Approval validation,
        identity locks, index activation, consumption and both audits share a
        single transaction; no readiness receipt grants a later permission.
        """
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding)
            if 'rollout_plan' in row['body']:
                raise ValueError('A rollout-bound release requires the rollout controller')
            live=[self._live(db,row,key) for key in ('a','b')]
            if row['state']!='approved' or not all(live):
                raise AuthorizationError('Two current administrator approvals are required')
            self.admin._activate(db,tenant_id=row['tenant_id'],index_id=row['candidate_index_id'],
                expected_active_index_id=row['previous_index_id'])
            # Manifest validation can take time: fail and roll back the switch
            # if either session or the release window expired during execution.
            live=[self._live(db,row,key) for key in ('a','b')]
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if not all(live) or row['expires_at']<=now:
                raise AuthorizationError('Release approval expired during execution')
            row['state'],row['version']='executed',row['version']+1
            db.execute(self.table.update().where(self.table.c.tenant_id==row['tenant_id'],
                self.table.c.release_id==row['release_id']).values(state='executed',version=row['version']))
            self.admin._audit(db,row['tenant_id'],row['release_id'],'release_execute',{
                'release_id':str(row['release_id']),'version':row['version'],
                'body_sha256':row['body_sha256'],'candidate_index_id':str(row['candidate_index_id'])})
            return self._summary(row,live)

    def reject(self,*,tenant_id,release_id,expected_version,expected_body_sha256,current_binding,token):
        """A designated live administrator may terminally withdraw publication.

        This is not an operational gray-rollout pause. A rejected definition
        cannot resume or execute; a new release and fresh approvals are needed.
        """
        if not isinstance(token,str):
            raise AuthenticationError()
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding)
            principal=self._principal(db,token)
            if (principal.tenant_id!=str(row['tenant_id']) or principal.user_id not in
                    (str(row['reviewer_a']),str(row['reviewer_b']))):
                raise AuthorizationError('Identity is not a designated reviewer')
            row['state'],row['version']='rejected',row['version']+1
            db.execute(self.table.update().where(self.table.c.tenant_id==row['tenant_id'],
                self.table.c.release_id==row['release_id']).values(state='rejected',version=row['version']))
            self.admin._audit(db,row['tenant_id'],row['release_id'],'release_reject',{
                'release_id':str(row['release_id']),'reviewer_user_id':principal.user_id,
                'version':row['version'],'body_sha256':row['body_sha256']})
            return {'release_id':str(row['release_id']),'tenant_id':str(row['tenant_id']),
                'state':'rejected','version':row['version'],'body_sha256':row['body_sha256'],
                'scope':'terminal_publication_withdrawal'}
