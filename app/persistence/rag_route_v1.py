"""Frozen narrow route projection. No table grants or runtime integration."""
ROUTE_FUNCTION_SQL = """
CREATE FUNCTION public.cs_rag_route(p_session uuid,p_session_hash text)
RETURNS TABLE(tenant_id uuid,rollout_id uuid,release_id uuid,route_version integer,index_id uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $function$
 WITH relevant AS (
   SELECT p_session_hash AS token_hash
   UNION
   SELECT unnest(ARRAY[i.approval_a_session_hash,i.approval_b_session_hash])
   FROM public.chat_sessions c
   JOIN public.rag_rollouts r ON r.tenant_id=c.tenant_id AND r.state='running'
   JOIN public.rag_release_intents i ON i.tenant_id=r.tenant_id AND i.release_id=r.release_id
   WHERE c.id=p_session
 ), live AS MATERIALIZED (
   SELECT s.token_hash,s.tenant_id,s.user_id,m.role
   FROM public.identity_sessions s JOIN relevant h ON h.token_hash=s.token_hash
   JOIN public.identity_users u ON u.id=s.user_id
   JOIN public.identity_memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
   JOIN public.identity_tenants t ON t.id=s.tenant_id
   WHERE s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
     AND s.auth_version=u.auth_version AND u.active AND m.active AND t.active
 ), scoped AS (
   SELECT c.id,c.tenant_id
   FROM public.chat_sessions c JOIN live actor ON actor.tenant_id=c.tenant_id
   WHERE c.id=p_session AND actor.token_hash=p_session_hash
     AND (c.owner_id=actor.user_id OR actor.role IN ('admin','support'))
     AND c.archived_at IS NULL AND c.deleted_at IS NULL
 ), eligible AS (
   SELECT r.*,i.candidate_index_id,i.previous_index_id,
     (('x'||substr(md5(c.tenant_id::text||':'||c.id::text||':'||r.rollout_id::text||':'||
                       (r.plan->>'cohort_salt')),1,8))::bit(32)::bigint % 10000) AS bucket
   FROM scoped c JOIN public.rag_rollouts r ON r.tenant_id=c.tenant_id
   JOIN public.rag_release_intents i ON i.tenant_id=r.tenant_id AND i.release_id=r.release_id
   JOIN live a ON a.token_hash=i.approval_a_session_hash AND a.tenant_id=i.tenant_id
     AND a.user_id=i.reviewer_a AND a.role='admin'
   JOIN live b ON b.token_hash=i.approval_b_session_hash AND b.tenant_id=i.tenant_id
     AND b.user_id=i.reviewer_b AND b.role='admin'
   JOIN public.knowledge_index_versions candidate ON candidate.tenant_id=i.tenant_id
     AND candidate.index_id=i.candidate_index_id AND candidate.status='ready'
   JOIN public.knowledge_index_versions previous ON previous.tenant_id=i.tenant_id
     AND previous.index_id=i.previous_index_id AND previous.status='active'
   WHERE r.state='running' AND i.state='approved'
     AND r.expires_at>clock_timestamp() AND i.expires_at>clock_timestamp()
     AND i.body->'rollout_plan'=r.plan
     AND i.body->'binding'->>'tenant_id'=i.tenant_id::text
     AND i.body->'binding'->>'index_id'=candidate.index_id::text
     AND i.body->'binding'->>'manifest_sha256'=candidate.manifest_sha256
     AND r.step_index>=0 AND r.basis_points BETWEEN 1 AND 9999
     AND r.plan->'steps_bps'->>r.step_index=r.basis_points::text
 )
 SELECT e.tenant_id,e.rollout_id,e.release_id,e.version,
   CASE WHEN e.bucket<e.basis_points THEN e.candidate_index_id ELSE e.previous_index_id END
 FROM eligible e
$function$;
"""
