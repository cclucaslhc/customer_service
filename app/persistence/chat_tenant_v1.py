"""Frozen definitions for the chat tenant migration; never a default login."""

LEGACY_TENANT_ID = "00000000-0000-0000-0000-000000000000"
CHAT_TENANT_TABLES = ("chat_sessions", "messages", "agent_runs", "agent_run_tools")
