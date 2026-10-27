"""No database/model calls: trusted context checks precede tool execution."""
from types import SimpleNamespace
import pytest
from app.agent import AgentDeps, lookup_order, lookup_shipping, check_refund_eligibility, search_knowledge
from app.identity import Principal, AuthorizationError, AuthenticationError


def test_authenticated_agent_does_not_return_shared_demo_business_records():
    principal = Principal("synthetic-user", "synthetic-tenant", "customer")
    ctx = SimpleNamespace(deps=AgentDeps(None, principal=principal, identity_resolver=lambda: principal))
    for result in (lookup_order(ctx, "A10001", "1234"), lookup_shipping(ctx, "A10001"), check_refund_eligibility(ctx, "A10001")):
        assert result["demo"] is True
        assert not result.get("found") and not result.get("eligible")
        assert "tracking_no" not in result and "product" not in result


def test_revoked_or_changed_agent_identity_denied_before_knowledge_tool():
    principal = Principal("synthetic-user", "synthetic-tenant", "customer")
    class Knowledge:
        def search(self, query):
            raise AssertionError("Identity denial must precede knowledge access")
    def revoked():
        raise AuthenticationError()
    for resolver, error in ((revoked, AuthenticationError),
            (lambda: Principal("other-user", "synthetic-tenant", "admin"), AuthorizationError),
            (lambda: Principal("synthetic-user", "other-tenant", "admin"), AuthorizationError)):
        ctx = SimpleNamespace(deps=AgentDeps(Knowledge(), principal=principal, identity_resolver=resolver))
        with pytest.raises(error):
            search_knowledge(ctx, "synthetic")
