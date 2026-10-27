"""Owner-controlled rollout transitions consumed by the scoped runtime route."""
import uuid
import sqlalchemy as sa
from app.identity import AuthorizationError
from app.persistence.schema import metadata
from app.rag_release_service import RagReleaseService, _freeze
from app.rag_rollout_plan import validate_plan


class RagRolloutService:
    def __init__(self,engine):
        self.engine=engine
        self.releases=RagReleaseService(engine)
        self.table=metadata.tables['rag_rollouts']

    def _guard(self,db):
        self.releases._guard(db)
        if not db.execute(sa.text("SELECT pg_get_userbyid(relowner)=current_user FROM pg_class "
                "WHERE oid='public.rag_rollouts'::regclass")).scalar_one():
            raise PermissionError('Rollout control requires the control-table owner')

    def _locked(self,db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256):
        if type(expected_rollout_version) is not int or expected_rollout_version<0:
            raise ValueError('Explicit rollout version required')
        row=db.execute(sa.select(self.table).where(
            self.table.c.tenant_id==uuid.UUID(str(tenant_id)),
            self.table.c.rollout_id==uuid.UUID(str(rollout_id))).with_for_update()).mappings().first()
        if not row or row['version']!=expected_rollout_version or row['plan_sha256']!=expected_plan_sha256:
            raise ValueError('Rollout receipt changed or unavailable')
        _,digest=_freeze(validate_plan(row['plan']))
        if digest!=row['plan_sha256']:
            raise ValueError('Rollout plan failed integrity verification')
        return dict(row)

    def _change(self,db,row,state,step,basis_points,action,*,observation=None):
        phase={}
        if state=='running' and (row['state']!='running' or row['step_index']!=step):
            phase={'step_started_at':sa.func.clock_timestamp()}
        row.update(state=state,step_index=step,basis_points=basis_points,version=row['version']+1)
        db.execute(self.table.update().where(self.table.c.tenant_id==row['tenant_id'],
            self.table.c.rollout_id==row['rollout_id']).values(**{key:row[key] for key in
                ('state','step_index','basis_points','version')},**phase))
        self.releases.admin._audit(db,row['tenant_id'],row['rollout_id'],'rollout_'+action,{
            'rollout_id':str(row['rollout_id']),'release_id':str(row['release_id']),
            'plan_sha256':row['plan_sha256'],'version':row['version'],
            'state':state,'step_index':step,'basis_points':basis_points,
            **({'observation':observation} if observation is not None else {})})
        return {'tenant_id':str(row['tenant_id']),'rollout_id':str(row['rollout_id']),
            'state':state,'version':row['version'],'step_index':step,'basis_points':basis_points,
            'plan_sha256':row['plan_sha256'],'scope':'rollout_control_state_not_runtime_routing'}

    def stage(self,*,rollout_id,tenant_id,release_id,expected_version,expected_body_sha256,current_binding):
        with self.engine.begin() as db:
            self._guard(db)
            row=self.releases._locked(db,tenant_id,release_id,expected_version,expected_body_sha256,current_binding)
            if row['state']!='approved' or not all(self.releases._live(db,row,key) for key in ('a','b')):
                raise AuthorizationError('Two current administrator approvals are required')
            if 'rollout_plan' not in row['body']:
                raise ValueError('Release has no approved rollout plan')
            plan,digest=_freeze(validate_plan(row['body']['rollout_plan']))
            rid=uuid.UUID(str(rollout_id))
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if now>=row['expires_at']:
                raise ValueError('Rollout approval window expired')
            db.execute(self.table.insert().values(tenant_id=row['tenant_id'],rollout_id=rid,
                release_id=row['release_id'],plan=plan,plan_sha256=digest,created_at=now,expires_at=row['expires_at']))
            self.releases.admin._audit(db,row['tenant_id'],rid,'rollout_stage',{
                'rollout_id':str(rid),'release_id':str(row['release_id']),
                'body_sha256':row['body_sha256'],'plan_sha256':digest})
            return {'tenant_id':str(row['tenant_id']),'rollout_id':str(rid),
                'release_id':str(row['release_id']),'state':'staged','version':0,'basis_points':0,
                'plan_sha256':digest,'scope':'rollout_staging_not_traffic_activation'}

    def start(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256,
              expected_release_version,expected_body_sha256,current_binding):
        """First step only. No arbitrary jump, resume, or final promotion."""
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state']!='staged':
                raise ValueError('Only a staged rollout can start')
            release=self.releases._locked(db,tenant_id,row['release_id'],expected_release_version,
                expected_body_sha256,current_binding)
            if (release['state']!='approved' or release['body'].get('rollout_plan')!=row['plan']
                    or not all(self.releases._live(db,release,key) for key in ('a','b'))):
                raise AuthorizationError('Current approvals must cover this exact rollout plan')
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if now>=row['expires_at']:
                raise ValueError('Rollout approval window expired')
            return self._change(db,row,'running',0,row['plan']['steps_bps'][0],'start')

    def pause(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256):
        """Owner stop does not depend on still-live reviewer sessions."""
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state']!='running':
                raise ValueError('Only a running rollout can pause')
            return self._change(db,row,'paused',row['step_index'],0,'pause')

    def rollback(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256):
        """Withdraw a non-completed allocation; not a completed-index rollback."""
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state'] not in ('staged','running','paused','recovery_review'):
                raise ValueError('Rollout cannot withdraw this terminal state')
            return self._change(db,row,'rolled_back',row['step_index'],0,'rollback')

    def prepare_reversion(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256,
                          expected_release_version,expected_body_sha256,new_index_id):
        """Create a fresh ready rollback identity, never reactivate a retired ID.

        Publication requires a NEW release, quality evidence and two approvals.
        The previous manifest must still describe current authorized documents.
        """
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state']!='completed':
                raise ValueError('Only a completed rollout can prepare a publication reversion')
            release=db.execute(sa.select(self.releases.table).where(
                self.releases.table.c.tenant_id==row['tenant_id'],
                self.releases.table.c.release_id==row['release_id']).with_for_update()).mappings().one()
            if (type(expected_release_version) is not int or release['version']!=expected_release_version
                    or release['body_sha256']!=expected_body_sha256 or release['state']!='executed'):
                raise ValueError('Executed release receipt changed')
            indexes=metadata.tables['knowledge_index_versions']
            current=db.execute(sa.select(indexes.c.index_id).where(indexes.c.tenant_id==row['tenant_id'],
                indexes.c.status=='active')).scalar_one_or_none()
            if current!=release['candidate_index_id']:
                raise ValueError('Published index changed; reversion requires fresh review')
            previous=db.execute(sa.select(indexes).where(indexes.c.tenant_id==row['tenant_id'],
                indexes.c.index_id==release['previous_index_id']).with_for_update()).mappings().one()
            if previous['status']!='retired':
                raise ValueError('Previous index is not retired')
            manifest,count=self.releases.admin._manifest(db,previous)
            if manifest!=previous['manifest_sha256']:
                raise ValueError('Previous index no longer covers current documents; rebuild and review')
            fresh=uuid.UUID(str(new_index_id))
            db.execute(indexes.insert().values(tenant_id=row['tenant_id'],index_id=fresh,
                profile_id=previous['profile_id'],status='ready',manifest_sha256=manifest,
                lexical_version=previous['lexical_version'],chunker_version=previous['chunker_version'],
                reranker_version=previous['reranker_version'],created_at=sa.func.clock_timestamp()))
            self.releases.admin._audit(db,row['tenant_id'],fresh,'reversion_prepare',{
                'rollout_id':str(row['rollout_id']),'release_id':str(row['release_id']),
                'source_index_id':str(previous['index_id']),'expected_active_index_id':str(current),
                'manifest_sha256':manifest,'current_chunks':count})
            return {'tenant_id':str(row['tenant_id']),'index_id':str(fresh),
                'expected_active_index_id':str(current),'manifest_sha256':manifest,
                'scope':'ready_reversion_candidate_requires_new_quality_and_approvals'}

    def observe(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256):
        """Read operational evidence from PG, not a caller-provided report."""
        from app.rag_rollout_observation import collect_window,assess_window
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state']!='running' or row['step_started_at'] is None:
                raise ValueError('Only a running rollout with a phase clock can be observed')
            report=collect_window(db,row)
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            return {'report':report,'assessment':assess_window(row['plan'],report,now=now),
                'scope':'operational_window_not_quality_review_or_release_authority'}

    def check_health_and_pause(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256,
                               expected_release_version,expected_body_sha256,current_binding):
        """Owner-only health tick. Never advances or resumes traffic.

        Stale caller receipts are errors, not evidence of an unhealthy release.
        Database failures propagate; only validated domain failures stop traffic.
        """
        from app.rag_rollout_observation import collect_window,assess_health
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            if row['state']!='running' or row['step_started_at'] is None:
                raise ValueError('Only a running rollout can be health checked')
            release=db.execute(sa.select(self.releases.table).where(
                self.releases.table.c.tenant_id==row['tenant_id'],
                self.releases.table.c.release_id==row['release_id']).with_for_update()).mappings().one()
            if (type(expected_release_version) is not int or release['version']!=expected_release_version
                    or release['body_sha256']!=expected_body_sha256):
                raise ValueError('Release receipt changed')
            reason=None
            try:
                checked=self.releases._locked(db,tenant_id,row['release_id'],expected_release_version,
                    expected_body_sha256,current_binding)
                if (checked['state']!='approved' or checked['body'].get('rollout_plan')!=row['plan']
                        or not all(self.releases._live(db,checked,key) for key in ('a','b'))):
                    reason='approval_invalid'
            except ValueError:
                reason='release_context_invalid'
            if reason:
                health={'decision':'pause','reason_codes':[reason]}
                observation={'health':health}
            else:
                report=collect_window(db,row)
                now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                health=assess_health(row['plan'],report,now=now)
                if not all(self.releases._live(db,checked,key) for key in ('a','b')):
                    health={**health,'decision':'pause','reason_codes':['approval_invalid']}
                observation={'report':report,'health':health}
            if health['decision']=='pause':
                receipt=self._change(db,row,'paused',row['step_index'],0,'health_pause',observation=observation)
            else:
                receipt={'tenant_id':str(row['tenant_id']),'rollout_id':str(row['rollout_id']),
                    'state':row['state'],'version':row['version'],'basis_points':row['basis_points'],
                    'plan_sha256':row['plan_sha256']}
            return {**receipt,'health':health,'scope':'atomic_health_tick_not_automatic_promotion'}

    def advance(self,*,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256,
                expected_release_version,expected_body_sha256,current_binding):
        """Recollect inside the transition transaction. Never accepts a report.

        Advances exactly one approved step. Final 100% promotes the index and
        consumes approvals atomically; failure never leaves half a publication.
        """
        from app.rag_rollout_observation import collect_window,assess_window
        from app.rag_release_gate import assess_quality
        with self.engine.begin() as db:
            self._guard(db)
            row=self._locked(db,tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256)
            steps=row['plan']['steps_bps']
            if (row['state']!='running' or row['step_started_at'] is None
                    or not 0<=row['step_index']<len(steps)-1
                    or row['basis_points']!=steps[row['step_index']]):
                raise ValueError('Only a consistent running phase can advance')
            release=self.releases._locked(db,tenant_id,row['release_id'],expected_release_version,
                expected_body_sha256,current_binding)
            if (release['state']!='approved' or release['body'].get('rollout_plan')!=row['plan']
                    or not all(self.releases._live(db,release,key) for key in ('a','b'))):
                raise AuthorizationError('Current approvals must cover this exact rollout plan')
            report=collect_window(db,row)
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            assessment=assess_window(row['plan'],report,now=now)
            if not assessment['thresholds_satisfied']:
                raise ValueError('Rollout observation gate refused: '+','.join(assessment['reason_codes']))
            next_step=row['step_index']+1
            final=steps[next_step]==10000
            if final:
                self.releases.admin._activate(db,tenant_id=row['tenant_id'],index_id=release['candidate_index_id'],
                    expected_active_index_id=release['previous_index_id'])
            # Manifest work can take time. Recheck evidence, session expiry,
            # observation freshness and deadline before consuming/advancing.
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            quality=assess_quality(release['body']['policy'],release['body']['evidence'],current_binding,now=now)
            if (now>=release['expires_at'] or not quality['thresholds_satisfied']
                    or not assess_window(row['plan'],report,now=now)['thresholds_satisfied']
                    or not all(self.releases._live(db,release,key) for key in ('a','b'))):
                raise AuthorizationError('Rollout evidence or approvals expired during transition')
            release_version=release['version']
            if final:
                release_version+=1
                table=self.releases.table
                db.execute(table.update().where(table.c.tenant_id==row['tenant_id'],
                    table.c.release_id==row['release_id']).values(state='executed',version=release_version))
                self.releases.admin._audit(db,row['tenant_id'],row['release_id'],'release_execute',{
                    'release_id':str(row['release_id']),'rollout_id':str(row['rollout_id']),
                    'version':release_version,'body_sha256':release['body_sha256'],
                    'observation_sha256':assessment['observation_sha256']})
            result=self._change(db,row,'completed' if final else 'running',next_step,steps[next_step],
                'complete' if final else 'advance',observation={'report':report,'assessment':assessment})
            return {**result,'release_version':release_version,'observation_sha256':assessment['observation_sha256'],
                'scope':'atomic_rollout_transition'}
