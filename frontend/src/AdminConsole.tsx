import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "./api";
import MarkdownContent from "./MarkdownContent";
import {
  adminPath,
  adminRouteFromPath,
  adminSessionActions,
  type AdminModule,
} from "./session";
import "./admin.css";

type ModuleKey = AdminModule;
type Paged<T> = { items: T[]; total: number; page: number; page_size: number };
type KnowledgeImage = { id: string; url: string; alt_text: string; location: string; width: number; height: number; media_type: string };
type SessionRow = {
  id: string;
  title: string;
  status: string;
  management_status: string;
  assignee?: string;
  tags: string[];
  archived_at?: string;
  deleted_at?: string;
  updated_at: string;
  message_count: number;
  run_count: number;
  error_count: number;
  contact_count: number;
  provider?: string;
  model?: string;
  last_run_status?: string;
  last_error_code?: string;
};
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  provider?: string;
  model?: string;
  citations?: { document: string; section?: string; excerpt: string; images?: KnowledgeImage[] }[];
};
type Run = {
  id: string;
  status: string;
  provider?: string;
  model?: string;
  prompt_version?: number;
  error_code?: string;
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
  tools: { tool_name: string; status: string }[];
};
type Contact = {
  id: string;
  session_id?: string;
  name: string;
  phone?: string;
  email?: string;
  summary: string;
  status: string;
  note?: string;
  created_at: string;
};
type SessionDetail = {
  session: SessionRow;
  messages: Message[];
  runs: Run[];
  contacts: Contact[];
  notes: { id: string; content: string; created_at: string }[];
  audit: AuditRow[];
};
type PromptVersion = {
  id: string;
  version: number;
  content: string;
  status: string;
  change_note: string;
  created_at: string;
  published_at?: string;
};
type PromptBundle = {
  baseline: string;
  current?: PromptVersion;
  versions: PromptVersion[];
};
type Provider = {
  provider: "deepseek" | "openai";
  model: string;
  configured: boolean;
  last_test_status?: string;
  last_test_message?: string;
};
type DocumentRow = {
  id: string;
  display_name: string;
  size: number;
  chunk_count: number;
  status: string;
  error_message?: string;
  detected_type?: string;
  source_path?: string;
  parser_version?: string;
  chunker_version?: string;
  version?: number;
  warnings?: string[];
  metadata?: Record<string, unknown>;
  created_at: string;
};
type KnowledgeImportIssue = { path: string; display_name: string; reason: string };
type KnowledgeJob = {
  id: string;
  status: "pending" | "running" | "complete" | "partial" | "failed";
  stage: string;
  total_count: number;
  processed_count: number;
  imported_count: number;
  skipped_count: number;
  failed_count: number;
  current_file?: string;
  error_message?: string;
  result?: { skipped?: KnowledgeImportIssue[]; failed?: KnowledgeImportIssue[] };
};
type AuditRow = {
  id: string;
  action: string;
  entity_type: string;
  entity_id?: string;
  summary: string;
  actor: string;
  created_at: string;
};
type Overview = {
  sessions: number;
  unresolved: number;
  runs: number;
  completion_rate: number;
  error_rate: number;
  contacts: number;
  documents: number;
  chunks: number;
  active_provider: string;
  providers: {
    provider: string;
    model: string;
    configured: boolean;
    last_test_status?: string;
    last_test_at?: string;
  }[];
  recent_errors: {
    id: string;
    session_id: string;
    error_code?: string;
    provider?: string;
    model?: string;
    started_at: string;
  }[];
};

const modules: {
  key: ModuleKey;
  label: string;
  eyebrow: string;
  description: string;
}[] = [
  {
    key: "overview",
    label: "运营概览",
    eyebrow: "OPERATIONS",
    description: "关键指标、服务质量与最近异常",
  },
  {
    key: "sessions",
    label: "会话管理",
    eyebrow: "CONVERSATIONS",
    description: "查看完整对话、运行轨迹并进行运营处置",
  },
  {
    key: "prompts",
    label: "系统提示词",
    eyebrow: "PROMPT CONTROL",
    description: "草稿、发布、版本比较与安全回滚",
  },
  {
    key: "knowledge",
    label: "知识库",
    eyebrow: "KNOWLEDGE",
    description: "文档导入、片段预览与检索测试",
  },
  {
    key: "models",
    label: "模型配置",
    eyebrow: "MODEL GATEWAY",
    description: "供应商、模型、密钥与连接状态",
  },
  {
    key: "contacts",
    label: "联系记录",
    eyebrow: "CONTACTS",
    description: "客户跟进状态、关联会话与内部备注",
  },
  {
    key: "audit",
    label: "审计日志",
    eyebrow: "AUDIT TRAIL",
    description: "追踪后台查看、变更、发布与导出操作",
  },
];

function routeState() {
  return adminRouteFromPath(window.location.pathname);
}

export default function AdminConsole() {
  const initial = routeState();
  const [module, setModule] = useState<ModuleKey>(initial.module);
  const [entityId, setEntityId] = useState(initial.entityId);
  const [notice, setNotice] = useState("");
  const meta = modules.find((item) => item.key === module)!;
  const navigate = (next: ModuleKey, id = "") => {
    const path = adminPath(next, id);
    window.history.pushState({}, "", path);
    setModule(next);
    setEntityId(id);
    setNotice("");
  };
  useEffect(() => {
    const restore = () => {
      const state = routeState();
      setModule(state.module);
      setEntityId(state.entityId);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  return (
    <div className="admin-console">
      <aside className="admin-nav">
        <div className="admin-nav-head">
          <strong>企业管理中心</strong>
          <small>仅限本机访问 · 完整数据模式</small>
        </div>
        {modules.map((item) => (
          <button
            className={module === item.key ? "active" : ""}
            key={item.key}
            onClick={() => navigate(item.key)}
          >
            <span>{item.label}</span>
            <small>{item.eyebrow}</small>
          </button>
        ))}
      </aside>
      <section className="admin-workspace">
        <header className="admin-page-head">
          <div>
            <span className="eyebrow">{meta.eyebrow}</span>
            <h1>{meta.label}</h1>
            <p>{meta.description}</p>
          </div>
          <div className="local-warning">
            ⚠ 未启用登录，仅允许 127.0.0.1 本机访问
          </div>
        </header>
        {notice && <div className="admin-notice">{notice}</div>}
        {module === "overview" && (
          <OverviewPage onOpenSession={(id) => navigate("sessions", id)} />
        )}
        {module === "sessions" && (
          <SessionsPage
            sessionId={entityId}
            onOpen={(id) => navigate("sessions", id)}
            onBack={() => navigate("sessions")}
            setNotice={setNotice}
          />
        )}
        {module === "prompts" && <PromptsPage setNotice={setNotice} />}
        {module === "knowledge" && <KnowledgePage setNotice={setNotice} />}
        {module === "models" && <ModelsPage setNotice={setNotice} />}
        {module === "contacts" && (
          <ContactsPage
            onOpenSession={(id) => navigate("sessions", id)}
            setNotice={setNotice}
          />
        )}
        {module === "audit" && <AuditPage />}
      </section>
    </div>
  );
}

function OverviewPage({
  onOpenSession,
}: {
  onOpenSession: (id: string) => void;
}) {
  const [data, setData] = useState<Overview | null>(null);
  useEffect(() => {
    void api<Overview>("/api/admin/overview").then(setData);
  }, []);
  if (!data) return <Loading />;
  const cards = [
    ["会话总数", data.sessions],
    ["未处理会话", data.unresolved],
    ["Agent 运行", data.runs],
    ["完成率", `${data.completion_rate}%`],
    ["错误率", `${data.error_rate}%`],
    ["转人工登记", data.contacts],
    ["知识文档", data.documents],
    ["知识片段", data.chunks],
  ];
  return (
    <div className="module-stack">
      <div className="metric-grid">
        {cards.map(([label, value]) => (
          <div className="metric-card" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <Panel title={`模型健康 · 当前 ${data.active_provider}`}>
        <div className="provider-health">
          {data.providers.map((provider) => (
            <div key={provider.provider}>
              <Status
                value={provider.configured ? "configured" : "unconfigured"}
              />
              <strong>
                {provider.provider} / {provider.model}
              </strong>
              <small>
                {provider.last_test_status
                  ? `上次测试：${provider.last_test_status} · ${formatTime(provider.last_test_at)}`
                  : "尚未测试连接"}
              </small>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="最近异常运行">
        <div className="data-list">
          {data.recent_errors.length ? (
            data.recent_errors.map((item) => (
              <button
                className="data-row clickable"
                key={item.id}
                onClick={() => onOpenSession(item.session_id)}
              >
                <div>
                  <strong>{item.error_code || "未知错误"}</strong>
                  <small>
                    {item.provider || "未记录"} / {item.model || "未记录"} ·{" "}
                    {formatTime(item.started_at)}
                  </small>
                </div>
                <span>查看会话 →</span>
              </button>
            ))
          ) : (
            <Empty text="暂无异常运行" />
          )}
        </div>
      </Panel>
    </div>
  );
}

function SessionsPage({
  sessionId,
  onOpen,
  onBack,
  setNotice,
}: {
  sessionId: string;
  onOpen: (id: string) => void;
  onBack: () => void;
  setNotice: (text: string) => void;
}) {
  if (sessionId)
    return (
      <SessionDetailPage
        sessionId={sessionId}
        onBack={onBack}
        setNotice={setNotice}
      />
    );
  const [rows, setRows] = useState<Paged<SessionRow>>({
    items: [],
    total: 0,
    page: 1,
    page_size: 25,
  });
  const [filters, setFilters] = useState({
    q: "",
    lifecycle: "all",
    management_status: "",
    assignee: "",
    tag: "",
    provider: "",
    run_status: "",
    has_contact: "",
    date_from: "",
    date_to: "",
  });
  const load = async (page = 1) => {
    const query = new URLSearchParams({ page: String(page), page_size: "25" });
    Object.entries(filters).forEach(([key, value]) => {
      if (value)
        query.set(
          key,
          key.startsWith("date_") ? new Date(value).toISOString() : value,
        );
    });
    setRows(await api<Paged<SessionRow>>(`/api/admin/chat/sessions?${query}`));
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="module-stack">
      <Panel title="检索与筛选">
        <div className="filter-grid">
          <input
            placeholder="搜索标题或对话正文"
            value={filters.q}
            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
          />
          <Select
            value={filters.lifecycle}
            onChange={(value) => setFilters({ ...filters, lifecycle: value })}
            options={[
              ["all", "全部生命周期"],
              ["active", "当前"],
              ["archived", "已归档"],
              ["deleted", "已删除"],
            ]}
          />
          <Select
            value={filters.management_status}
            onChange={(value) =>
              setFilters({ ...filters, management_status: value })
            }
            options={[
              ["", "全部管理状态"],
              ["open", "待处理"],
              ["pending", "跟进中"],
              ["resolved", "已解决"],
            ]}
          />
          <input
            placeholder="负责人"
            value={filters.assignee}
            onChange={(e) =>
              setFilters({ ...filters, assignee: e.target.value })
            }
          />
          <input
            placeholder="标签"
            value={filters.tag}
            onChange={(e) => setFilters({ ...filters, tag: e.target.value })}
          />
          <Select
            value={filters.provider}
            onChange={(value) => setFilters({ ...filters, provider: value })}
            options={[
              ["", "全部模型"],
              ["deepseek", "DeepSeek"],
              ["openai", "OpenAI"],
            ]}
          />
          <Select
            value={filters.run_status}
            onChange={(value) => setFilters({ ...filters, run_status: value })}
            options={[
              ["", "全部运行结果"],
              ["complete", "完成"],
              ["error", "错误"],
              ["interrupted", "中断"],
            ]}
          />
          <Select
            value={filters.has_contact}
            onChange={(value) => setFilters({ ...filters, has_contact: value })}
            options={[
              ["", "全部转人工状态"],
              ["true", "已登记联系"],
              ["false", "未登记联系"],
            ]}
          />
          <label className="date-filter">
            更新时间从
            <input
              type="datetime-local"
              value={filters.date_from}
              onChange={(e) =>
                setFilters({ ...filters, date_from: e.target.value })
              }
            />
          </label>
          <label className="date-filter">
            更新时间至
            <input
              type="datetime-local"
              value={filters.date_to}
              onChange={(e) =>
                setFilters({ ...filters, date_to: e.target.value })
              }
            />
          </label>
          <button className="primary" onClick={() => void load()}>
            应用筛选
          </button>
        </div>
      </Panel>
      <Panel title={`会话列表 · ${rows.total}`}>
        <div className="session-admin-list">
          {rows.items.map((item) => (
            <button
              className="session-admin-row"
              key={item.id}
              onClick={() => onOpen(item.id)}
            >
              <div className="session-title">
                <strong>{item.title}</strong>
                <small>
                  {formatTime(item.updated_at)} · {item.message_count} 条消息
                </small>
                <div className="tag-line">
                  {item.tags.map((tag) => (
                    <span key={tag}>{tag}</span>
                  ))}
                </div>
              </div>
              <div>
                <Status value={item.management_status} />
                <small>{item.assignee || "未分配负责人"}</small>
              </div>
              <div className="session-numbers">
                <span>运行 {item.run_count}</span>
                <span className={item.error_count ? "error-text" : ""}>
                  错误 {item.error_count}
                </span>
                <span>联系 {item.contact_count}</span>
              </div>
              <div>
                <small>
                  {item.provider
                    ? `${item.provider} / ${item.model}`
                    : "暂无模型调用"}
                </small>
                {item.last_error_code && (
                  <b className="error-text">{item.last_error_code}</b>
                )}
              </div>
              <span>详情 →</span>
            </button>
          ))}
        </div>
        <Pager data={rows} onPage={(page) => void load(page)} />
      </Panel>
    </div>
  );
}

function SessionDetailPage({
  sessionId,
  onBack,
  setNotice,
}: {
  sessionId: string;
  onBack: () => void;
  setNotice: (text: string) => void;
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [note, setNote] = useState("");
  const [tags, setTags] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busyAction, setBusyAction] = useState<
    "" | "archive" | "restore" | "delete"
  >("");
  const [actionError, setActionError] = useState("");
  const load = async () => {
    const data = await api<SessionDetail>(
      `/api/admin/chat/sessions/${sessionId}`,
    );
    setDetail(data);
    setTags(data.session.tags.join(", "));
  };
  useEffect(() => {
    void load();
  }, [sessionId]);
  if (!detail) return <Loading />;
  const update = async (values: Record<string, unknown>) => {
    await api(
      `/api/admin/chat/sessions/${sessionId}`,
      jsonRequest("PATCH", values),
    );
    setNotice("会话管理信息已更新");
    await load();
  };
  const addNote = async () => {
    if (!note.trim()) return;
    await api(
      `/api/admin/chat/sessions/${sessionId}/notes`,
      jsonRequest("POST", { content: note }),
    );
    setNote("");
    setNotice("内部备注已添加");
    await load();
  };
  const lifecycle = async (action: "archive" | "restore" | "delete") => {
    setBusyAction(action);
    setActionError("");
    try {
      await api(
        `/api/admin/chat/sessions/${sessionId}${action === "delete" ? "" : `/${action}`}`,
        { method: action === "delete" ? "DELETE" : "POST" },
      );
      setNotice(
        action === "archive"
          ? "会话已归档"
          : action === "restore"
            ? "会话已恢复"
            : "会话已安全删除，可恢复",
      );
      setConfirmDelete(false);
      await load();
    } catch (error) {
      setActionError(
        error instanceof ApiError ? error.message : "操作失败，请稍后重试",
      );
    } finally {
      setBusyAction("");
    }
  };
  const actions = adminSessionActions(detail.session);
  return (
    <div className="module-stack">
      <div className="detail-toolbar">
        <button onClick={onBack}>← 返回列表</button>
        <div>
          <a
            href={`/api/admin/chat/sessions/${sessionId}/export?format=markdown`}
          >
            导出 Markdown
          </a>
          <a href={`/api/admin/chat/sessions/${sessionId}/export?format=csv`}>
            导出 CSV
          </a>
          {actions.lifecycle === "deleted" && (
            <span className="deleted-label">已安全删除</span>
          )}
          {actions.canRestore && (
            <button
              disabled={Boolean(busyAction)}
              onClick={() => void lifecycle("restore")}
            >
              {busyAction === "restore" ? "恢复中…" : "恢复"}
            </button>
          )}
          {actions.canArchive && (
            <button
              disabled={Boolean(busyAction)}
              onClick={() => void lifecycle("archive")}
            >
              {busyAction === "archive" ? "归档中…" : "归档"}
            </button>
          )}
          {actions.canDelete && (
            <button
              className="danger"
              disabled={Boolean(busyAction)}
              onClick={() => {
                setActionError("");
                setConfirmDelete(true);
              }}
            >
              安全删除
            </button>
          )}
        </div>
      </div>
      {actionError && (
        <div className="admin-action-error" role="alert">
          {actionError}
        </div>
      )}
      {confirmDelete && (
        <div
          className="confirm-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target && !busyAction)
              setConfirmDelete(false);
          }}
        >
          <section
            className="confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-session-title"
          >
            <span className="confirm-icon">!</span>
            <div>
              <h2 id="delete-session-title">确认安全删除此会话？</h2>
              <p>
                会话将从普通列表隐藏，但消息、运行记录和联系信息仍会保留，之后可在“已删除”筛选中恢复。
              </p>
            </div>
            <div className="confirm-actions">
              <button
                disabled={Boolean(busyAction)}
                onClick={() => setConfirmDelete(false)}
              >
                取消
              </button>
              <button
                className="danger-solid"
                disabled={Boolean(busyAction)}
                onClick={() => void lifecycle("delete")}
              >
                {busyAction === "delete" ? "删除中…" : "确认安全删除"}
              </button>
            </div>
          </section>
        </div>
      )}
      <Panel title={detail.session.title}>
        <div className="management-grid">
          <label>
            管理状态
            <Select
              value={detail.session.management_status}
              onChange={(value) => void update({ management_status: value })}
              options={[
                ["open", "待处理"],
                ["pending", "跟进中"],
                ["resolved", "已解决"],
              ]}
            />
          </label>
          <label>
            负责人
            <input
              defaultValue={detail.session.assignee || ""}
              onBlur={(e) => void update({ assignee: e.target.value })}
            />
          </label>
          <label>
            标签（逗号分隔）
            <div className="inline-field">
              <input value={tags} onChange={(e) => setTags(e.target.value)} />
              <button
                onClick={() =>
                  void update({
                    tags: tags
                      .split(",")
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })
                }
              >
                保存
              </button>
            </div>
          </label>
          <div className="fact-box">
            <span>技术状态</span>
            <strong>{detail.session.status}</strong>
            <small>更新于 {formatTime(detail.session.updated_at)}</small>
          </div>
        </div>
      </Panel>
      <div className="detail-columns">
        <Panel title={`完整对话 · ${detail.messages.length}`}>
          <div className="admin-transcript">
            {detail.messages.map((message) => (
              <article className={message.role} key={message.id}>
                <header>
                  <strong>
                    {message.role === "user" ? "客户" : "AI 客服"}
                  </strong>
                  <small>
                    {formatTime(message.created_at)}
                    {message.provider
                      ? ` · ${message.provider}/${message.model}`
                      : ""}
                  </small>
                </header>
                <MarkdownContent>{message.content}</MarkdownContent>
                {message.citations?.length ? (
                  <details>
                    <summary>引用资料 {message.citations.length} 条</summary>
                    {message.citations.map((citation, index) => (
                      <div key={index}>
                        <p><b>{citation.document} / {citation.section || "正文"}</b><br />{citation.excerpt}</p>
                        {citation.images?.length ? <KnowledgeImages images={citation.images} /> : null}
                      </div>
                    ))}
                  </details>
                ) : null}
              </article>
            ))}
          </div>
        </Panel>
        <div className="side-stack">
          <Panel title="运行轨迹">
            <div className="timeline">
              {detail.runs.map((run) => (
                <details key={run.id}>
                  <summary>
                    <Status value={run.status} />
                    <span>
                      {run.provider || "未知"} / {run.model || "未知"}
                    </span>
                  </summary>
                  <p>
                    提示词：
                    {run.prompt_version
                      ? `v${run.prompt_version}`
                      : "历史内置版本"}{" "}
                    · 耗时：
                    {run.duration_ms == null ? "—" : `${run.duration_ms} ms`}
                  </p>
                  {run.error_code && (
                    <p className="error-text">错误：{run.error_code}</p>
                  )}
                  <div className="tag-line">
                    {run.tools.map((tool, index) => (
                      <span key={`${tool.tool_name}-${index}`}>
                        {tool.tool_name} · {tool.status}
                      </span>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          </Panel>
          <Panel title="客户联系记录">
            {detail.contacts.length ? (
              detail.contacts.map((contact) => (
                <div className="compact-record" key={contact.id}>
                  <strong>{contact.name}</strong>
                  <span>{contact.phone || contact.email}</span>
                  <p>{contact.summary}</p>
                </div>
              ))
            ) : (
              <Empty text="没有联系登记" />
            )}
          </Panel>
          <Panel title="内部备注">
            <div className="inline-field">
              <textarea
                placeholder="仅管理员可见"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <button className="primary" onClick={() => void addNote()}>
                添加
              </button>
            </div>
            {detail.notes.map((item) => (
              <div className="compact-record" key={item.id}>
                <small>{formatTime(item.created_at)}</small>
                <p>{item.content}</p>
              </div>
            ))}
          </Panel>
          <Panel title="最近审计">
            {detail.audit.slice(0, 8).map((item) => (
              <div className="compact-record" key={item.id}>
                <strong>{item.summary}</strong>
                <small>{formatTime(item.created_at)}</small>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function PromptsPage({ setNotice }: { setNotice: (text: string) => void }) {
  const [bundle, setBundle] = useState<PromptBundle | null>(null);
  const [content, setContent] = useState("");
  const [note, setNote] = useState("");
  const [compareId, setCompareId] = useState("");
  const load = async () => {
    const data = await api<PromptBundle>("/api/admin/prompts");
    setBundle(data);
    setContent((current) => current || data.current?.content || "");
  };
  useEffect(() => {
    void load();
  }, []);
  if (!bundle) return <Loading />;
  const selected = bundle.versions.find((item) => item.id === compareId);
  const saveDraft = async () => {
    const draft = await api<PromptVersion>(
      "/api/admin/prompts/drafts",
      jsonRequest("POST", { content, change_note: note }),
    );
    setNotice(`草稿 v${draft.version} 已保存`);
    setNote("");
    await load();
  };
  const publish = async (id: string) => {
    await api(`/api/admin/prompts/${id}/publish`, { method: "POST" });
    setNotice("提示词已发布，新会话运行立即生效");
    setContent("");
    await load();
  };
  const rollback = async (id: string) => {
    await api(
      `/api/admin/prompts/${id}/rollback`,
      jsonRequest("POST", { change_note: `从后台回滚到所选版本` }),
    );
    setNotice("已创建新版本并完成回滚发布");
    setContent("");
    await load();
  };
  return (
    <div className="module-stack">
      <Panel title={`当前发布版本 · v${bundle.current?.version || 0}`}>
        <div className="prompt-layout">
          <div>
            <label>
              企业客服提示词
              <textarea
                className="prompt-editor"
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
            </label>
            <div className="editor-meta">
              <span>{content.length} / 20,000 字符</span>
              <input
                placeholder="填写本次变更说明"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <button
                className="primary"
                disabled={content.trim().length < 20}
                onClick={() => void saveDraft()}
              >
                保存为新草稿
              </button>
            </div>
          </div>
          <details className="baseline">
            <summary>查看不可编辑的安全基线</summary>
            <pre>{bundle.baseline}</pre>
          </details>
        </div>
      </Panel>
      <Panel title="版本历史">
        <div className="version-list">
          {bundle.versions.map((version) => (
            <div className={`version-row ${version.status}`} key={version.id}>
              <div>
                <strong>
                  v{version.version} · {statusLabel(version.status)}
                </strong>
                <small>
                  {version.change_note || "无变更说明"} ·{" "}
                  {formatTime(version.created_at)}
                </small>
              </div>
              <div>
                <button onClick={() => setCompareId(version.id)}>对比</button>
                {version.status === "draft" && (
                  <button
                    className="primary"
                    onClick={() => void publish(version.id)}
                  >
                    发布
                  </button>
                )}
                {version.status === "archived" && (
                  <button onClick={() => void rollback(version.id)}>
                    回滚到此版本
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      {selected && (
        <Panel
          title={`版本对比：当前 v${bundle.current?.version} ↔ v${selected.version}`}
        >
          <div className="diff-grid">
            <div>
              <strong>当前发布版本</strong>
              <pre>{bundle.current?.content}</pre>
            </div>
            <div>
              <strong>所选版本</strong>
              <pre>{selected.content}</pre>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

function KnowledgePage({ setNotice }: { setNotice: (text: string) => void }) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [path, setPath] = useState("");
  const [termsText, setTermsText] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<
    { display_name: string; section?: string; content: string; score: number; relevance?: number; ranking?: { matched_synonyms?: string[] }; images?: KnowledgeImage[] }[]
  >([]);
  const [importIssues, setImportIssues] = useState<
    { path: string; display_name: string; reason: string; kind: "跳过" | "失败" }[]
  >([]);
  const [importJob, setImportJob] = useState<KnowledgeJob | null>(null);
  const [detail, setDetail] = useState<{
    document: DocumentRow;
    chunks: {
      id: number;
      section?: string;
      location?: string;
      block_type?: string;
      char_count?: number;
      content: string;
    }[];
    assets: KnowledgeImage[];
  } | null>(null);
  const load = async () => {
    setDocuments(await api<DocumentRow[]>("/api/admin/documents"));
    const location = await api<{ path: string }>(
      "/api/admin/knowledge/location",
    );
    setPath((current) => current || location.path);
    const terms = await api<{ custom: string[][] }>("/api/admin/knowledge/terms");
    setTermsText(terms.custom.map((group) => group.join(" = ")).join("\n"));
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!importJob || !["pending", "running"].includes(importJob.status)) return;
    const timer = window.setInterval(async () => {
      const job = await api<KnowledgeJob>(`/api/admin/knowledge/jobs/${importJob.id}`);
      setImportJob(job);
      if (!["pending", "running"].includes(job.status)) {
        const skipped = job.result?.skipped || [];
        const failed = job.result?.failed || [];
        setImportIssues([
          ...skipped.map((item) => ({ ...item, kind: "跳过" as const })),
          ...failed.map((item) => ({ ...item, kind: "失败" as const })),
        ]);
        setNotice(`导入 ${job.imported_count}，跳过 ${job.skipped_count}，失败 ${job.failed_count}`);
        await load();
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [importJob?.id, importJob?.status]);
  const importPath = async () => {
    const job = await api<KnowledgeJob>(
      "/api/admin/knowledge/jobs",
      jsonRequest("POST", { path, recursive: true }),
    );
    setImportIssues([]);
    setImportJob(job);
    setNotice("导入任务已创建，可继续使用管理界面");
  };
  const upload = async (files: File[]) => {
    for (const file of files) {
      const body = new FormData();
      body.append("file", file);
      await api("/api/admin/documents", { method: "POST", body });
    }
    setNotice(`已上传 ${files.length} 个文档`);
    await load();
  };
  const search = async () => {
    const result = await api<{ items: typeof hits }>(
      "/api/admin/knowledge/search-test",
      jsonRequest("POST", { query, limit: 8 }),
    );
    setHits(result.items);
  };
  const remove = async (id: string) => {
    await api(`/api/admin/documents/${id}`, { method: "DELETE" });
    setDetail(null);
    setNotice("知识文档已删除");
    await load();
  };
  return (
    <div className="module-stack">
      <Panel title="导入与索引">
        <div className="knowledge-toolbar">
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="本机文件或目录路径"
          />
          <button className="primary" disabled={!!importJob && ["pending", "running"].includes(importJob.status)} onClick={() => void importPath()}>
            {importJob && ["pending", "running"].includes(importJob.status) ? "正在后台导入…" : "扫描并导入"}
          </button>
          <label className="file-button">
            选择文件
            <input
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.wps,.rtf,.odt,.xls,.xlsx,.et,.ods,.csv,.tsv,.ppt,.pptx,.dps,.odp,.md,.txt,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp"
              onChange={(e) => {
                const files = Array.from(e.currentTarget.files || []);
                e.currentTarget.value = "";
                void upload(files);
              }}
            />
          </label>
          <label className="file-button">
            选择目录
            <input
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.wps,.rtf,.odt,.xls,.xlsx,.et,.ods,.csv,.tsv,.ppt,.pptx,.dps,.odp,.md,.txt,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp"
              ref={(element) => element?.setAttribute("webkitdirectory", "")}
              onChange={(e) => {
                const files = Array.from(e.currentTarget.files || []);
                e.currentTarget.value = "";
                void upload(files);
              }}
            />
          </label>
          <button
            onClick={async () => {
              const result = await api<{ indexed_chunks: number }>(
                "/api/admin/index/rebuild",
                { method: "POST" },
              );
              setNotice(`已重建 ${result.indexed_chunks} 个片段索引`);
            }}
          >
            重建索引
          </button>
        </div>
        {importJob && (
          <div className="job-progress" aria-live="polite">
            <div><strong>{importJob.stage}</strong><span>{importJob.processed_count}/{importJob.total_count}</span></div>
            <progress value={importJob.processed_count} max={Math.max(importJob.total_count, 1)} />
            {importJob.current_file && <small>{importJob.current_file}</small>}
            {importJob.error_message && <small className="error-text">{importJob.error_message}</small>}
          </div>
        )}
        <details className="terms-editor">
          <summary>企业术语与同义词</summary>
          <p className="muted">每行一组，用等号或逗号分隔，例如：产品规格书 = datasheet = 数据手册。</p>
          <textarea value={termsText} onChange={(event) => setTermsText(event.target.value)} rows={5} placeholder="术语 = 同义词 = abbreviation" />
          <button onClick={async () => {
            const groups = termsText.split(/\r?\n/).map((line) => line.split(/[=,，]/).map((value) => value.trim()).filter(Boolean)).filter((group) => group.length >= 2);
            await api("/api/admin/knowledge/terms", jsonRequest("PUT", { groups }));
            setNotice(`已保存 ${groups.length} 组企业术语`);
          }}>保存术语</button>
        </details>
        {importIssues.length > 0 && (
          <div className="import-report">
            <strong>逐文件处理结果</strong>
            {importIssues.map((item, index) => (
              <div className={`import-issue ${item.kind === "失败" ? "failed" : "skipped"}`} key={`${item.path}-${index}`}>
                <span>{item.kind}</span>
                <div><b>{item.display_name || item.path}</b><small>{item.reason}</small></div>
              </div>
            ))}
          </div>
        )}
      </Panel>
      <div className="detail-columns">
        <Panel title={`知识文档 · ${documents.length}`}>
          <div className="data-list">
            {documents.map((doc) => (
              <button
                className="data-row clickable"
                key={doc.id}
                onClick={async () =>
                  setDetail(await api(`/api/admin/documents/${doc.id}`))
                }
              >
                <div>
                  <strong>{doc.display_name}</strong>
                  <small>
                    {(doc.size / 1024).toFixed(1)} KB · {doc.chunk_count} 个片段
                    · {doc.detected_type || doc.status} · v{doc.version || 1}
                  </small>
                </div>
                <span>预览 →</span>
              </button>
            ))}
          </div>
        </Panel>
        <div className="side-stack">
          <Panel title="检索测试">
            <div className="inline-field">
              <input
                placeholder="输入查询词"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button className="primary" onClick={() => void search()}>
                检索
              </button>
            </div>
            {hits.map((hit, index) => (
              <div className="search-hit" key={index}>
                <strong>
                  {hit.display_name} / {hit.section || "正文"}
                </strong>
                <small>相关度 {hit.relevance?.toFixed(2) || "-"}{hit.ranking?.matched_synonyms?.length ? ` · ${hit.ranking.matched_synonyms.join("；")}` : ""}</small>
                <p>{hit.content}</p>
                {hit.images?.length ? <KnowledgeImages images={hit.images} /> : null}
              </div>
            ))}
          </Panel>
          {detail && (
            <Panel title={detail.document.display_name}>
              <p className="muted">
                {detail.document.detected_type || detail.document.status} · 解析器 {detail.document.parser_version || "1.0"}
                {detail.document.source_path ? ` · ${detail.document.source_path}` : ""}
              </p>
              {!!detail.document.warnings?.length && (
                <div className="import-report">
                  {detail.document.warnings.map((warning, index) => <div className="import-issue skipped" key={index}>{warning}</div>)}
                </div>
              )}
              {!!detail.assets.length && <div className="knowledge-gallery"><strong>文档图片 · {detail.assets.length}</strong><KnowledgeImages images={detail.assets} /></div>}
              <div className="chunk-preview">
                {detail.chunks.map((chunk, index) => (
                  <details key={chunk.id}>
                    <summary>
                      片段 {index + 1} ·{" "}
                      {chunk.section || chunk.location || "正文"} · {chunk.block_type || "文本"}
                    </summary>
                    <p>{chunk.content}</p>
                  </details>
                ))}
              </div>
              <button
                onClick={async () => {
                  await api(`/api/admin/documents/${detail.document.id}/reparse`, { method: "POST" });
                  setNotice("已使用当前解析器重新建库");
                  setDetail(await api(`/api/admin/documents/${detail.document.id}`));
                  await load();
                }}
              >
                重新解析
              </button>
              <button
                className="danger"
                onClick={() => void remove(detail.document.id)}
              >
                删除此文档
              </button>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function KnowledgeImages({ images }: { images: KnowledgeImage[] }) {
  return <div className="knowledge-images">{images.map(image => <figure key={image.id}><img src={image.url} alt={image.alt_text} width={image.width} height={image.height} loading="lazy" decoding="async" referrerPolicy="no-referrer"/><figcaption>{image.location}</figcaption></figure>)}</div>;
}

function ModelsPage({ setNotice }: { setNotice: (text: string) => void }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [active, setActive] = useState("");
  const [models, setModels] = useState<Record<string, string>>({});
  const [keys, setKeys] = useState<Record<string, string>>({});
  const load = async () => {
    const data = await api<{ active_provider: string; providers: Provider[] }>(
      "/api/admin/providers",
    );
    setProviders(data.providers);
    setActive(data.active_provider);
    setModels(
      Object.fromEntries(
        data.providers.map((item) => [item.provider, item.model]),
      ),
    );
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="provider-page">
      {providers.map((provider) => (
        <Panel
          title={provider.provider === "deepseek" ? "DeepSeek" : "OpenAI"}
          key={provider.provider}
        >
          <div className="provider-status">
            <Status
              value={provider.configured ? "configured" : "unconfigured"}
            />
            <span>
              {active === provider.provider ? "当前使用" : "备用供应商"}
            </span>
          </div>
          <label>
            模型名称
            <input
              value={models[provider.provider] || ""}
              onChange={(e) =>
                setModels({ ...models, [provider.provider]: e.target.value })
              }
            />
          </label>
          <label>
            API Key（只写，不回显）
            <input
              type="password"
              autoComplete="new-password"
              value={keys[provider.provider] || ""}
              placeholder={
                provider.configured ? "留空表示不修改" : "请输入密钥"
              }
              onChange={(e) =>
                setKeys({ ...keys, [provider.provider]: e.target.value })
              }
            />
          </label>
          <div className="button-row">
            <button
              onClick={async () => {
                const result = await api<{ message: string }>(
                  `/api/admin/providers/${provider.provider}/test`,
                  jsonRequest("POST", {
                    model: models[provider.provider],
                    api_key: keys[provider.provider] || null,
                  }),
                );
                setNotice(result.message);
                await load();
              }}
            >
              测试连接
            </button>
            <button
              onClick={async () => {
                await api(
                  `/api/admin/providers/${provider.provider}`,
                  jsonRequest("PUT", {
                    model: models[provider.provider],
                    api_key: keys[provider.provider] || null,
                  }),
                );
                setKeys({ ...keys, [provider.provider]: "" });
                setNotice("模型配置已安全保存");
                await load();
              }}
            >
              保存配置
            </button>
            <button
              className="primary"
              disabled={!provider.configured || active === provider.provider}
              onClick={async () => {
                await api(
                  "/api/admin/active-provider",
                  jsonRequest("PUT", { provider: provider.provider }),
                );
                setNotice("已切换当前供应商");
                await load();
              }}
            >
              设为当前
            </button>
          </div>
          {provider.last_test_message && (
            <p className="muted">上次测试：{provider.last_test_message}</p>
          )}
        </Panel>
      ))}
    </div>
  );
}

function ContactsPage({
  onOpenSession,
  setNotice,
}: {
  onOpenSession: (id: string) => void;
  setNotice: (text: string) => void;
}) {
  const [data, setData] = useState<Paged<Contact>>({
    items: [],
    total: 0,
    page: 1,
    page_size: 25,
  });
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const load = async (page = 1) => {
    const query = new URLSearchParams({ page: String(page), page_size: "25" });
    if (q) query.set("q", q);
    if (status) query.set("status", status);
    setData(await api(`/api/admin/contacts?${query}`));
  };
  useEffect(() => {
    void load();
  }, []);
  const update = async (id: string, values: Record<string, unknown>) => {
    await api(`/api/admin/contacts/${id}`, jsonRequest("PATCH", values));
    setNotice("联系记录已更新");
    await load(data.page);
  };
  return (
    <div className="module-stack">
      <Panel title="搜索联系记录">
        <div className="filter-grid">
          <input
            placeholder="姓名、电话、邮箱或咨询摘要"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Select
            value={status}
            onChange={setStatus}
            options={[
              ["", "全部状态"],
              ["new", "待跟进"],
              ["following", "跟进中"],
              ["closed", "已关闭"],
            ]}
          />
          <button className="primary" onClick={() => void load()}>
            搜索
          </button>
        </div>
      </Panel>
      <Panel title={`联系记录 · ${data.total}`}>
        <div className="contact-admin-grid">
          {data.items.map((contact) => (
            <article key={contact.id}>
              <header>
                <div>
                  <strong>{contact.name}</strong>
                  <small>{contact.phone || contact.email}</small>
                </div>
                <Select
                  value={contact.status}
                  onChange={(value) =>
                    void update(contact.id, { status: value })
                  }
                  options={[
                    ["new", "待跟进"],
                    ["following", "跟进中"],
                    ["closed", "已关闭"],
                  ]}
                />
              </header>
              <p>{contact.summary}</p>
              <textarea
                defaultValue={contact.note || ""}
                placeholder="内部跟进备注"
                onBlur={(e) =>
                  void update(contact.id, { note: e.target.value })
                }
              />
              {contact.session_id && (
                <button onClick={() => onOpenSession(contact.session_id!)}>
                  查看关联会话 →
                </button>
              )}
            </article>
          ))}
        </div>
        <Pager data={data} onPage={(page) => void load(page)} />
      </Panel>
    </div>
  );
}

function AuditPage() {
  const [data, setData] = useState<Paged<AuditRow>>({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
  });
  const [action, setAction] = useState("");
  const load = async (page = 1) => {
    const query = new URLSearchParams({ page: String(page), page_size: "50" });
    if (action) query.set("action", action);
    setData(await api(`/api/admin/audit?${query}`));
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="module-stack">
      <Panel title="审计筛选">
        <div className="inline-field">
          <input
            placeholder="按动作筛选，如 prompt、session"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          />
          <button className="primary" onClick={() => void load()}>
            筛选
          </button>
        </div>
      </Panel>
      <Panel title={`审计事件 · ${data.total}`}>
        <div className="audit-table">
          {data.items.map((item) => (
            <div className="audit-row" key={item.id}>
              <code>{item.action}</code>
              <div>
                <strong>{item.summary}</strong>
                <small>
                  {item.entity_type}
                  {item.entity_id ? ` / ${item.entity_id}` : ""}
                </small>
              </div>
              <span>{item.actor}</span>
              <time>{formatTime(item.created_at)}</time>
            </div>
          ))}
        </div>
        <Pager data={data} onPage={(page) => void load(page)} />
      </Panel>
    </div>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="admin-panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
function Loading() {
  return <div className="admin-loading">正在加载管理数据…</div>;
}
function Empty({ text }: { text: string }) {
  return <div className="admin-empty">{text}</div>;
}
function Status({ value }: { value: string }) {
  return (
    <span className={`status-pill status-${value}`}>{statusLabel(value)}</span>
  );
}
function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(([key, label]) => (
        <option value={key} key={key}>
          {label}
        </option>
      ))}
    </select>
  );
}
function Pager<T>({
  data,
  onPage,
}: {
  data: Paged<T>;
  onPage: (page: number) => void;
}) {
  const pages = Math.ceil(data.total / data.page_size);
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>
        上一页
      </button>
      <span>
        {data.page} / {pages}
      </span>
      <button
        disabled={data.page >= pages}
        onClick={() => onPage(data.page + 1)}
      >
        下一页
      </button>
    </div>
  );
}
function jsonRequest(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}
function statusLabel(value: string) {
  return (
    (
      {
        open: "待处理",
        pending: "跟进中",
        resolved: "已解决",
        running: "运行中",
        complete: "完成",
        error: "错误",
        interrupted: "已中断",
        configured: "已配置",
        unconfigured: "未配置",
        published: "已发布",
        draft: "草稿",
        archived: "历史版本",
        new: "待跟进",
        following: "跟进中",
        closed: "已关闭",
      } as Record<string, string>
    )[value] || value
  );
}
