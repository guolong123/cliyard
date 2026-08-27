/**
 * cliyard-web API client
 * =============================================
 * 对齐后端 src/cliyard/server/api/* 路由契约（同源：Vite proxy /api → :8080）。
 * 类型结构对应 schema_bridge.build_command_tree 输出；SSE 事件遵循 executor
 * 约定：统一 {"type", **payload, "time"}，终态 done/error 后事件流收尾。
 */

/* ---------------------------------- 类型（对齐后端响应） ---------------------------------- */

/** 命令树叶子：YAML resource.method（schema_bridge 输出） */
export interface TreeItem {
  name: string;
  labels: string[];
  desc: string;
  path: string;
  method: string;
  /** JSON Schema（params_to_json_schema 产物，T10 表单渲染用） */
  schema: Record<string, unknown>;
}

/** 子资源（对应 YAML resource 文件，挂在一个 group 下） */
export interface GroupResource {
  name: string;
  desc: string;
  commands: TreeItem[];
}

/** 资源分组：两级结构（group → resources），commands 为拍平兼容字段 */
export interface Group {
  group: string;
  desc: string;
  commands: TreeItem[];
  /** 两级分组下的子资源；扁平组（无 group 字段）为单元素或缺失 */
  resources?: GroupResource[];
}

/** flow 元数据（_flows.yaml 注册） */
export interface Flow {
  name: string;
  description: string;
  command: string;
  category: string;
  category_label: string;
  labels: string[];
  params_schema: Record<string, unknown>;
  step_count: number;
}

/** _auth.yaml web 品牌配置 */
export interface WebBranding {
  logo_url?: string;
  logo_text?: string;
  title?: string;
  subtitle?: string;
}

/** GET /api/spec 顶层结构 */
export interface SpecData {
  service: {
    name: string;
    description: string;
    web?: {
      branding?: WebBranding;
    };
  };
  groups: Group[];
  flows: Flow[];
}

/** SSE 执行事件：{"type", **payload, "time"}（validate/auth/request/response/format/done/error） */
export interface TableColumn {
  name: string;
  alias: string;
}

/** format 事件可选 table 字段：YAML output 定义的结构化表格数据（列头=alias，行=格式化值） */
export interface TableData {
  columns: TableColumn[];
  rows: string[][];
  total?: number;
}

export interface ExecutionEvent {
  type: string;
  time: string;
  /** format 事件可选：仅当命令带 output.items_path + fields 时由后端填充 */
  table?: TableData;
  [k: string]: unknown;
}

/** GET /api/executions/{id} 轮询兜底响应（SSE 断线后读取已记录事件） */
export interface ExecutionDetail {
  id: string;
  kind: string;
  target: string;
  status: string;
  created_at: string;
  steps: ExecutionEvent[];
}

/** 历史记录条目（T6 SQLite history 落地后的形态） */
export interface HistoryItem {
  id: string;
  created_at: string;
  kind: string;
  target: string;
  status: string;
  /** 终态才有耗时；running 等未完成执行返回 null */
  duration_ms: number | null;
  result_preview: string;
}

/** GET /api/auth/profiles 响应（token 一律 masked，后端保证） */
export interface AuthProfile {
  name: string;
  endpoint: string;
  token_masked: string;
  expires_at?: number;
  auth_username?: string;
}

export interface ProfileList {
  current: AuthProfile | null;
  profiles: AuthProfile[];
}

/** GET /api/auth/environments 响应 */
export interface EnvPreset {
  name: string;
  endpoint: string;
  endpoints?: Record<string, string>;
  default_username?: string;
  default_password?: string;
}

/* ---------------------------------- 内部工具 ---------------------------------- */

/** 统一 fetch：非 2xx 时解析 FastAPI {"detail"} 错误体并抛 Error */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* 非 JSON 错误体，保留 statusText */
    }
    throw new Error(`${res.status} ${detail}`.trim());
  }
  return (await res.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/* ---------------------------------- spec ---------------------------------- */

/** GET /api/spec：命令树 + flow 元数据 */
export function fetchSpec(): Promise<SpecData> {
  return request<SpecData>("/api/spec");
}

/* ---------------------------------- 执行 / SSE ---------------------------------- */

/** POST /api/execute：提交命令/流程执行，立即返回 execution_id */
export function execute(
  kind: "command" | "flow",
  target: string,
  params: Record<string, unknown> = {},
): Promise<{ execution_id: string }> {
  return request<{ execution_id: string }>(
    "/api/execute",
    jsonInit("POST", { kind, target, params }),
  );
}

/**
 * 订阅执行 SSE 事件流（GET /api/executions/{id}/stream）。
 * onmessage 解析 ``data: {...}`` 帧；收到 done/error 后自动 close。
 * 返回取消函数（幂等，可随时断开）。
 */
export function streamExecution(
  id: string,
  onEvent: (event: ExecutionEvent) => void,
): () => void {
  const source = new EventSource(`/api/executions/${id}/stream`);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };
  source.onmessage = (e: MessageEvent<string>) => {
    let event: ExecutionEvent;
    try {
      event = JSON.parse(e.data) as ExecutionEvent;
    } catch {
      return; // 非 JSON 帧（心跳等）忽略
    }
    onEvent(event);
    if (event.type === "done" || event.type === "error") close();
  };
  // 网络错误/断线：EventSource 会自动重连，这里保持订阅；
  // 仅显式 close（onerror 时 EventSource 已断开，无需二次 close）
  source.onerror = () => {
    close();
  };
  return close;
}

/** GET /api/executions/{id}：轮询兜底（当前状态 + 全量 steps） */
export function fetchExecution(id: string): Promise<ExecutionDetail> {
  return request<ExecutionDetail>(`/api/executions/${id}`);
}

/* ---------------------------------- 历史（T6 后端落地后生效） ---------------------------------- */

/** GET /api/executions：历史列表（time desc，分页 + kind 过滤） */
export function listExecutions(
  limit = 20,
  offset = 0,
  kind?: "command" | "flow",
): Promise<{ total: number; items: HistoryItem[] }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (kind) params.set("kind", kind);
  return request<{ total: number; items: HistoryItem[] }>(`/api/executions?${params.toString()}`);
}

/** DELETE /api/executions：清空历史 */
export async function clearExecutions(): Promise<void> {
  await request<unknown>("/api/executions", { method: "DELETE" });
}

/** 重放一次历史执行：调用后端 replay 接口（用原始 params 重新提交） */
export function replayExecution(id: string): Promise<{ execution_id: string }> {
  return request<{ execution_id: string }>(`/api/executions/${id}/replay`, { method: "POST" });
}

/* ---------------------------------- 认证 profile ---------------------------------- */

/** GET /api/auth/profiles：列出（masked）+ 当前 profile */
export function getProfiles(): Promise<ProfileList> {
  return request<ProfileList>("/api/auth/profiles");
}

/** POST /api/auth/switch：切换当前 profile */
export function switchProfile(name: string): Promise<{ current: string }> {
  return request<{ current: string }>("/api/auth/switch", jsonInit("POST", { profile: name }));
}

/** POST /api/auth/login：登录并保存 profile */
export function loginAuth(body: {
  username: string;
  password: string;
  endpoint: string;
  endpoints?: Record<string, string>;
  env_name?: string;
  profile_name?: string;
}): Promise<{ profile: string; expires_at?: number }> {
  return request<{ profile: string; expires_at?: number }>("/api/auth/login", jsonInit("POST", body));
}

/** POST /api/auth/refresh：续签指定 profile */
export function refreshAuth(profile: string, password?: string): Promise<{ profile: string; expires_at?: number }> {
  return request<{ profile: string; expires_at?: number }>(
    "/api/auth/refresh",
    jsonInit("POST", { profile, password }),
  );
}

/** DELETE /api/auth/profile：删除指定 profile */
export function deleteAuth(profile: string): Promise<{ deleted: string; auto_switched_to?: string }> {
  return request<{ deleted: string; auto_switched_to?: string }>(
    `/api/auth/profile?profile=${encodeURIComponent(profile)}`,
    { method: "DELETE" },
  );
}

/** GET /api/auth/environments：读取 spec 目录的环境预设 */
export function fetchEnvironments(): Promise<{ environments: EnvPreset[] }> {
  return request<{ environments: EnvPreset[] }>("/api/auth/environments");
}

/* ---------------------------------- 收藏夹 ---------------------------------- */

export interface Favorite {
  name: string;
  target: string;
  group: string;
  /** 后端 Pydantic 归一化为空字符串，前端保留为空字符串以对齐 */
  description: string;
}

/** GET /api/favorites：读取收藏夹 */
export function fetchFavorites(): Promise<{ favorites: Favorite[] }> {
  return request<{ favorites: Favorite[] }>("/api/favorites");
}

/** POST /api/favorites：保存收藏夹（全量替换） */
export function saveFavorites(
  favorites: Favorite[],
): Promise<{ status: string; count: number }> {
  return request<{ status: string; count: number }>(
    "/api/favorites",
    jsonInit("POST", { favorites }),
  );
}

/** POST /api/favorites/toggle：增量添加/移除一条收藏（H3 缓解） */
export function toggleFavorite(
  target: string,
  item?: Favorite,
): Promise<{ status: string; action: string; count: number }> {
  return request<{ status: string; action: string; count: number }>(
    "/api/favorites/toggle",
    jsonInit("POST", item ? { target, item } : { target }),
  );
}
