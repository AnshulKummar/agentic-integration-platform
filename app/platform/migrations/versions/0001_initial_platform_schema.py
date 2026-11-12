"""initial platform schema

Revision ID: 0001
Revises:
Create Date: 2026-11-12 13:54:17.257697

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Row-level security (PostgreSQL only): defense in depth behind the app's tenant
# filters. Frozen from app.platform.auth.rls at the time of this revision.
RLS_UP = [
    "ALTER TABLE api_releases ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE api_releases FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON api_releases",
    "CREATE POLICY tenant_isolation ON api_releases USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE approvals ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE approvals FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON approvals",
    "CREATE POLICY tenant_isolation ON approvals USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON audit_events",
    "CREATE POLICY tenant_isolation ON audit_events USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE connections ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE connections FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON connections",
    "CREATE POLICY tenant_isolation ON connections USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE context_integrations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE context_integrations FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON context_integrations",
    "CREATE POLICY tenant_isolation ON context_integrations USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE deployment_health_checks ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE deployment_health_checks FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployment_health_checks",
    "CREATE POLICY tenant_isolation ON deployment_health_checks USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE deployment_stages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE deployment_stages FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployment_stages",
    "CREATE POLICY tenant_isolation ON deployment_stages USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE deployments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE deployments FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployments",
    "CREATE POLICY tenant_isolation ON deployments USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE executions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE executions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON executions",
    "CREATE POLICY tenant_isolation ON executions USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE integration_version_plans ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE integration_version_plans FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integration_version_plans",
    "CREATE POLICY tenant_isolation ON integration_version_plans USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE integration_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE integration_versions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integration_versions",
    "CREATE POLICY tenant_isolation ON integration_versions USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE integrations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE integrations FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integrations",
    "CREATE POLICY tenant_isolation ON integrations USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE kg_edges ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE kg_edges FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON kg_edges",
    "CREATE POLICY tenant_isolation ON kg_edges USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE kg_nodes ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE kg_nodes FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON kg_nodes",
    "CREATE POLICY tenant_isolation ON kg_nodes USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE memberships ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON memberships",
    "CREATE POLICY tenant_isolation ON memberships USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON refresh_tokens",
    "CREATE POLICY tenant_isolation ON refresh_tokens USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE roadmap_items ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE roadmap_items FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON roadmap_items",
    "CREATE POLICY tenant_isolation ON roadmap_items USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
    "ALTER TABLE secrets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE secrets FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON secrets",
    "CREATE POLICY tenant_isolation ON secrets USING ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on')) WITH CHECK ((tenant_id = current_setting('app.tenant_id', true)) OR (current_setting('app.rls_bypass', true) = 'on'))",
]
RLS_DOWN = [
    "DROP POLICY IF EXISTS tenant_isolation ON api_releases",
    "ALTER TABLE api_releases NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE api_releases DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON approvals",
    "ALTER TABLE approvals NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE approvals DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON audit_events",
    "ALTER TABLE audit_events NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON connections",
    "ALTER TABLE connections NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE connections DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON context_integrations",
    "ALTER TABLE context_integrations NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE context_integrations DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployment_health_checks",
    "ALTER TABLE deployment_health_checks NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE deployment_health_checks DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployment_stages",
    "ALTER TABLE deployment_stages NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE deployment_stages DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deployments",
    "ALTER TABLE deployments NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE deployments DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON executions",
    "ALTER TABLE executions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE executions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integration_version_plans",
    "ALTER TABLE integration_version_plans NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE integration_version_plans DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integration_versions",
    "ALTER TABLE integration_versions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE integration_versions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON integrations",
    "ALTER TABLE integrations NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE integrations DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON kg_edges",
    "ALTER TABLE kg_edges NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE kg_edges DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON kg_nodes",
    "ALTER TABLE kg_nodes NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE kg_nodes DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON memberships",
    "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON refresh_tokens",
    "ALTER TABLE refresh_tokens NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE refresh_tokens DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON roadmap_items",
    "ALTER TABLE roadmap_items NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE roadmap_items DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON secrets",
    "ALTER TABLE secrets NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE secrets DISABLE ROW LEVEL SECURITY",
]


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("suite", sa.String(length=32), nullable=False),
        sa.Column("predictor", sa.String(length=32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("eval_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_eval_runs_created_at"), ["created_at"], unique=False)

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("learning_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "api_releases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("system_id", sa.String(length=100), nullable=False),
        sa.Column("app", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("previous_version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("mcp_tools", sa.JSON(), nullable=False),
        sa.Column("impact", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("api_releases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_api_releases_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_events_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "connections",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("system_id", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("auth", sa.JSON(), nullable=False),
        sa.Column("tool_prefix", sa.String(length=64), nullable=False),
        sa.Column("health_path", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "system_id", name="uq_connections_tenant_system"),
    )
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_connections_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "context_integrations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("roadmap_item_id", sa.String(length=32), nullable=True),
        sa.Column("pattern_id", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("uses", sa.JSON(), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=False),
        sa.Column("target_system", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    with op.batch_alter_table("context_integrations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_context_integrations_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "executions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("integration_id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("deployment_id", sa.String(length=32), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("executions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_executions_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_executions_deployment_id"), ["deployment_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_executions_integration_id"), ["integration_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_executions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_executions_version_id"), ["version_id"], unique=False)

    op.create_table(
        "integrations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=False),
        sa.Column("target_system", sa.String(length=100), nullable=False),
        sa.Column("trigger", sa.String(length=300), nullable=False),
        sa.Column("initiated_by", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("roadmap_item_id", sa.String(length=32), nullable=True),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("active_version_id", sa.String(length=32), nullable=True),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_integrations_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_claim", ["status", "run_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "kg_edges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("edge_id", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("learned_in", sa.String(length=32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "edge_id"),
    )
    with op.batch_alter_table("kg_edges", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_kg_edges_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "kg_nodes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("system", sa.String(length=100), nullable=True),
        sa.Column("learned_in", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "node_id"),
    )
    with op.batch_alter_table("kg_nodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_kg_nodes_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "role", name="uq_membership_tenant_user_role"),
    )
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_memberships_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_memberships_user_id"), ["user_id"], unique=False)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_refresh_tokens_family_id"), ["family_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_refresh_tokens_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_refresh_tokens_user_id"), ["user_id"], unique=False)

    op.create_table(
        "roadmap_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("column", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("quarter", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("requires", sa.JSON(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("fit_rationale", sa.Text(), nullable=True),
        sa.Column("integration_id", sa.String(length=32), nullable=True),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("roadmap_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_roadmap_items_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "secrets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=12), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_secrets_tenant_name"),
    )
    with op.batch_alter_table("secrets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_secrets_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "integration_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("integration_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("mappings", sa.JSON(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("human_edits", sa.Integer(), nullable=False),
        sa.Column("pattern_reused", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_id", "number"),
    )
    with op.batch_alter_table("integration_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_integration_versions_integration_id"), ["integration_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_integration_versions_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("approver_id", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["integration_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "role"),
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approvals_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_approvals_version_id"), ["version_id"], unique=False)

    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("integration_id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("previous_version_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=16), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("initiated_by", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canary_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watch_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["integration_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deployments_integration_id"), ["integration_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployments_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployments_version_id"), ["version_id"], unique=False)

    op.create_table(
        "integration_version_plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("edit_log", sa.JSON(), nullable=False),
        sa.Column("learned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("learning_report", sa.JSON(), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["integration_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id"),
    )
    with op.batch_alter_table("integration_version_plans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_integration_version_plans_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_integration_version_plans_version_id"), ["version_id"], unique=False)

    op.create_table(
        "deployment_health_checks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("deployment_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("check", sa.String(length=64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("observed", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deployment_health_checks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deployment_health_checks_deployment_id"), ["deployment_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployment_health_checks_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "deployment_stages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("deployment_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "stage"),
    )
    with op.batch_alter_table("deployment_stages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deployment_stages_deployment_id"), ["deployment_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deployment_stages_tenant_id"), ["tenant_id"], unique=False)

    # ### end Alembic commands ###
    if op.get_bind().dialect.name == "postgresql":
        for stmt in RLS_UP:
            op.execute(stmt)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for stmt in RLS_DOWN:
            op.execute(stmt)
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("deployment_stages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployment_stages_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_deployment_stages_deployment_id"))

    op.drop_table("deployment_stages")
    with op.batch_alter_table("deployment_health_checks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployment_health_checks_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_deployment_health_checks_deployment_id"))

    op.drop_table("deployment_health_checks")
    with op.batch_alter_table("integration_version_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integration_version_plans_version_id"))
        batch_op.drop_index(batch_op.f("ix_integration_version_plans_tenant_id"))

    op.drop_table("integration_version_plans")
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployments_version_id"))
        batch_op.drop_index(batch_op.f("ix_deployments_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_deployments_integration_id"))

    op.drop_table("deployments")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approvals_version_id"))
        batch_op.drop_index(batch_op.f("ix_approvals_tenant_id"))

    op.drop_table("approvals")
    with op.batch_alter_table("integration_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integration_versions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_integration_versions_integration_id"))

    op.drop_table("integration_versions")
    with op.batch_alter_table("secrets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_secrets_tenant_id"))

    op.drop_table("secrets")
    with op.batch_alter_table("roadmap_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roadmap_items_tenant_id"))

    op.drop_table("roadmap_items")
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_family_id"))

    op.drop_table("refresh_tokens")
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_memberships_user_id"))
        batch_op.drop_index(batch_op.f("ix_memberships_tenant_id"))

    op.drop_table("memberships")
    with op.batch_alter_table("kg_nodes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_kg_nodes_tenant_id"))

    op.drop_table("kg_nodes")
    with op.batch_alter_table("kg_edges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_kg_edges_tenant_id"))

    op.drop_table("kg_edges")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_tenant_id"))
        batch_op.drop_index("ix_jobs_claim")

    op.drop_table("jobs")
    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integrations_tenant_id"))

    op.drop_table("integrations")
    with op.batch_alter_table("executions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_executions_version_id"))
        batch_op.drop_index(batch_op.f("ix_executions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_executions_integration_id"))
        batch_op.drop_index(batch_op.f("ix_executions_deployment_id"))
        batch_op.drop_index(batch_op.f("ix_executions_created_at"))

    op.drop_table("executions")
    with op.batch_alter_table("context_integrations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_context_integrations_tenant_id"))

    op.drop_table("context_integrations")
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_connections_tenant_id"))

    op.drop_table("connections")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_created_at"))

    op.drop_table("audit_events")
    with op.batch_alter_table("api_releases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_releases_tenant_id"))

    op.drop_table("api_releases")
    op.drop_table("users")
    op.drop_table("tenants")
    with op.batch_alter_table("eval_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_eval_runs_created_at"))

    op.drop_table("eval_runs")
    # ### end Alembic commands ###
