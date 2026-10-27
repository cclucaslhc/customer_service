"""Frozen transaction-held identity locks for the narrow route projection."""
ROUTE_LOCK_FUNCTION_SQL = """
CREATE FUNCTION public.cs_rag_route_lock(p_session uuid,p_session_hash text)
RETURNS TABLE(tenant_id uuid,rollout_id uuid,release_id uuid,route_version integer,index_id uuid)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $function$
DECLARE v_tenant uuid; v_user uuid; v_release uuid; v_users uuid[]; v_hashes text[];
BEGIN
 PERFORM pg_advisory_xact_lock_shared(1129533503);
 SELECT s.tenant_id,s.user_id INTO v_tenant,v_user
 FROM public.identity_sessions s
 JOIN public.identity_users u ON u.id=s.user_id
 JOIN public.identity_memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
 JOIN public.identity_tenants t ON t.id=s.tenant_id
 JOIN public.chat_sessions c ON c.tenant_id=s.tenant_id AND c.id=p_session
 WHERE s.token_hash=p_session_hash AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
   AND s.auth_version=u.auth_version AND u.active AND m.active AND t.active
   AND (c.owner_id=s.user_id OR m.role IN ('admin','support'))
   AND c.archived_at IS NULL AND c.deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'Route identity unavailable' USING ERRCODE='28000'; END IF;
 SELECT r.release_id INTO v_release FROM public.cs_rag_route(p_session,p_session_hash) r;
 v_users=ARRAY[v_user]; v_hashes=ARRAY[p_session_hash];
 IF v_release IS NOT NULL THEN
   SELECT v_users||ARRAY[i.reviewer_a,i.reviewer_b],
          v_hashes||ARRAY[i.approval_a_session_hash,i.approval_b_session_hash]
     INTO v_users,v_hashes FROM public.rag_release_intents i
     WHERE i.tenant_id=v_tenant AND i.release_id=v_release;
 END IF;
 -- SHARE permits simultaneous readers and FK KEY SHARE, but blocks actual
 -- revocation updates. Match the tenant -> users -> memberships -> sessions
 -- order; never acquire a reviewer session before its membership.
 PERFORM 1 FROM public.identity_tenants t WHERE t.id=v_tenant FOR SHARE;
 PERFORM 1 FROM public.identity_users u WHERE u.id=ANY(v_users) ORDER BY u.id FOR SHARE;
 PERFORM 1 FROM public.identity_memberships m WHERE m.tenant_id=v_tenant
   AND m.user_id=ANY(v_users) ORDER BY m.user_id FOR SHARE;
 PERFORM 1 FROM public.identity_sessions s WHERE s.token_hash=ANY(v_hashes) ORDER BY s.token_hash FOR SHARE;
 IF NOT EXISTS (
   SELECT 1 FROM public.identity_sessions s
   JOIN public.identity_users u ON u.id=s.user_id
   JOIN public.identity_memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
   JOIN public.identity_tenants t ON t.id=s.tenant_id
   WHERE s.token_hash=p_session_hash AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
     AND s.auth_version=u.auth_version AND u.active AND m.active AND t.active
 ) THEN RAISE EXCEPTION 'Route identity unavailable' USING ERRCODE='28000'; END IF;
 RETURN QUERY SELECT * FROM public.cs_rag_route(p_session,p_session_hash);
END
$function$;
"""
