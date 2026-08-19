import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { streamExecution } from "../api/client";
import type { ExecutionEvent, TableColumn, TableData } from "../api/client";
import HistoryPanel from "./HistoryPanel";
import type { HistoryPanelHandle } from "./HistoryPanel";
import {
  brand,
  neutral,
  space,
  radius,
  fontSize,
  fontFamily,
  shadow,
  statusColors,
} from "../styles/tokens";

const baseFont: CSSProperties = { fontFamily: fontFamily.body };

const cardBase: CSSProperties = {
  backgroundColor: "#FFFFFF",
  border: `1px solid ${neutral[200]}`,
  borderRadius: radius.lg,
  boxShadow: shadow.sm,
};

export interface StepsPanelProps {
  executionId: string | null;
  onReExecute: (executionId?: string) => void;
}

type StepStatus = "done" | "running" | "error";

interface StepCard {
  key: string;
  title: string;
  time: string;
  status: StepStatus;
  isDoneEvent: boolean;
  /** 始终可见的使用/耗时/结果行 */
  summaryLines: string[];
  /** 输入参数键值对（参数详情区域） */
  paramsEntries: [string, string][];
  /** pipeline 事件列表（请求详情区域） */
  pipelineEvents: ExecutionEvent[];
  /** echo 日志（日志区域） */
  logs: string[];
  /** format 事件的结构化表格数据 */
  table?: TableData;
  /** format 事件的 JSON 预览行 */
  formatLines: string[];
  /** 内嵌的文本表格（如 rich Table 字符串） */
  tableString?: string;
}

/** ISO 时间 → "HH:MM:SS.mmm" */
function timeToDisplay(iso: string): string {
  return iso.length >= 23 ? iso.slice(11, 23) : iso;
}

/** 值 → 单行字符串 */
function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

/** Clean rich markup tags from echo messages */
function cleanEcho(msg: string): string {
  return msg.replace(/\[.*?\]/g, "").trim();
}

/** Parse a JSON preview string into flat key-value entries */
function parsePreviewToEntries(preview: string, maxLen = 500): [string, string][] {
  if (!preview || preview === "{}" || preview === "null" || preview === "undefined") return [];
  try {
    const obj = JSON.parse(preview);
    if (typeof obj !== "object" || obj === null) return [];
    const entries: [string, string][] = [];
    for (const [k, v] of Object.entries(obj)) {
      const vs = formatValue(v);
      if (vs && vs !== "null" && vs !== "undefined") {
        entries.push([k, vs.length > maxLen ? vs.slice(0, maxLen) + "…" : vs]);
      }
    }
    return entries;
  } catch {
    return [["", preview.length > maxLen ? preview.slice(0, maxLen) + "…" : preview]];
  }
}

/** pipeline 事件 → 一行文本 */
function pipelineToLine(ev: ExecutionEvent): string {
  switch (ev.type) {
    case "validate": {
      const params = ev.params as Record<string, Record<string, unknown>> | undefined;
      if (!params || typeof params !== "object") return "参数校验";
      const count = Object.values(params).reduce((s, kv) => s + (kv && typeof kv === "object" ? Object.keys(kv).length : 0), 0);
      return `参数校验（${count} 个参数）`;
    }
    case "auth": {
      const mode = formatValue(ev.mode) || "chain";
      const pf = Array.isArray(ev.pre_filled_keys) ? (ev.pre_filled_keys as string[]).join(",") : "";
      return `认证准备（${mode}${pf ? ` · 预填: ${pf}` : ""}）`;
    }
    case "request": {
      return `发送请求 ${String(ev.method ?? "GET")} ${String(ev.url ?? "")}`;
    }
    case "response": {
      return `等待响应 HTTP ${String(ev.status_code ?? "?")} · ${String(ev.elapsed_ms ?? "?")}ms`;
    }
    case "format": {
      return "格式化结果";
    }
    default:
      return ev.type;
  }
}

// ----- 折叠面板组件 -----

function CollapsiblePanel({
  title,
  count,
  children,
  defaultOpen = false,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ marginTop: space.sm }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: space.xs,
          padding: `${space.xs}px ${space.sm}px`,
          width: "100%",
          border: `1px solid ${neutral[200]}`,
          borderRadius: radius.md,
          backgroundColor: neutral[50],
          cursor: "pointer",
          fontFamily: fontFamily.body,
          fontSize: fontSize.xs,
          color: neutral[500],
          transition: "background-color .15s ease",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = neutral[100]; }}
        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = neutral[50]; }}
      >
        <span
          style={{
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform .15s ease",
            fontSize: 10,
            color: neutral[400],
          }}
        >
          ▶
        </span>
        <span>
          {title}
          {count !== undefined ? `（${count}）` : ""}
        </span>
      </button>
      {open && (
        <div
          style={{
            padding: space.sm,
            backgroundColor: neutral[900],
            borderRadius: `0 0 ${radius.md}px ${radius.md}px`,
            border: `1px solid ${neutral[200]}`,
            borderTop: "none",
            maxHeight: 300,
            overflowY: "auto",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

/** 深色背景行 */
function DarkLine({ text, dim }: { text: string; dim?: boolean }) {
  return (
    <div
      style={{
        padding: "1px 0",
        fontFamily: fontFamily.mono,
        fontSize: fontSize.xs,
        lineHeight: 1.6,
        color: dim ? neutral[500] : neutral[300],
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
      }}
    >
      {text}
    </div>
  );
}

/** 参数键值对行 */
function KVPair({ k, v }: { k: string; v: string }) {
  return (
    <div
      style={{
        padding: "1px 0",
        fontFamily: fontFamily.mono,
        fontSize: fontSize.xs,
        lineHeight: 1.6,
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
      }}
    >
      <span style={{ color: "#7DD3FC" }}>{k}</span>
      <span style={{ color: neutral[400] }}> = </span>
      <span style={{ color: "#6EE7B7" }}>{v}</span>
    </div>
  );
}

// ----- 步骤卡片渲染 -----

/** 步骤状态图标 */
function StepIcon({ status, isDoneEvent }: { status: StepStatus; isDoneEvent?: boolean }) {
  const base: CSSProperties = {
    display: "flex",
    width: 24,
    height: 24,
    flexShrink: 0,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "50%",
    position: "relative",
  };
  if (status === "error")
    return (
      <span data-testid="step-icon" data-status="error" style={{ ...base, backgroundColor: statusColors.error.color, color: "#FFFFFF" }}>
        <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </span>
    );
  if (status === "running")
    return (
      <span data-testid="step-icon" data-status="running" style={{ ...base, backgroundColor: brand[50], border: `1.5px solid ${brand[500]}`, color: brand[500] }}>
        <span
          aria-hidden
          style={{
            width: 6, height: 6, borderRadius: "50%", backgroundColor: brand[500],
            animation: "cliyard-breathe 1.2s ease-in-out infinite",
          }}
        />
      </span>
    );
  return (
    <span data-testid="step-icon" data-status="done" style={{
      ...base,
      backgroundColor: isDoneEvent ? brand[500] : statusColors.success.color,
      color: "#FFFFFF",
    }}>
      <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    </span>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "center",
      minHeight: 160, margin: space.lg, borderRadius: radius.md,
      border: `1px dashed ${neutral[200]}`, backgroundColor: neutral[50],
      color: neutral[400], fontSize: fontSize.sm, ...baseFont,
    }}>
      {text}
    </div>
  );
}

// ----- Table 组件（复用现有逻辑） -----

function ResultTable({ columns, rows }: { columns: TableColumn[]; rows: string[][] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: fontFamily.mono, fontSize: fontSize.xs }}>
        <thead>
          <tr style={{ backgroundColor: neutral[50] }}>
            {columns.map((c, ci) => (
              <th key={ci} style={{
                textAlign: "left", padding: `${space.sm}px ${space.md}px`,
                borderBottom: `1px solid ${neutral[200]}`, fontWeight: 600,
                color: neutral[700], whiteSpace: "nowrap",
              }}>
                {c.alias}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="cliyard-table-row" style={{ borderBottom: `1px solid ${neutral[100]}` }}>
              {row.map((cell, ci) => (
                <td key={ci} style={{ padding: `${space.sm}px ${space.md}px`, color: neutral[700], whiteSpace: "nowrap" }}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FormatCardBody({ table, lines }: { table: TableData; lines: string[] }) {
  const [view, setView] = useState<"table" | "json">("table");
  return (
    <>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: `${space.xs}px ${space.sm}px`, borderBottom: `1px solid ${neutral[200]}`,
        backgroundColor: neutral[50],
      }}>
        <span style={{ fontSize: fontSize.xs, color: neutral[500], ...baseFont }}>
          {table.total !== undefined ? `共 ${String(table.total)} 条` : "表格视图"}
        </span>
        <div style={{ display: "flex", gap: space.xs }}>
          {(["table", "json"] as const).map((v) => (
            <button
              key={v} type="button"
              data-testid={`format-view-${v}`}
              data-active={view === v ? "true" : "false"}
              onClick={() => setView(v)}
              className="cliyard-text-btn"
              style={{
                padding: "1px 8px", fontSize: fontSize.xs,
                backgroundColor: view === v ? brand[50] : "transparent",
                color: view === v ? brand[600] : neutral[500],
                fontWeight: view === v ? 600 : 400,
                borderRadius: radius.sm,
              }}
            >
              {v === "table" ? "表格" : "JSON"}
            </button>
          ))}
        </div>
      </div>
      {view === "table" ? (
        <ResultTable columns={table.columns} rows={table.rows} />
      ) : (
        <div style={{
          padding: space.sm, backgroundColor: neutral[900],
          fontFamily: fontFamily.mono, fontSize: fontSize.xs,
          color: "#6EE7B7", maxHeight: 300, overflowY: "auto",
        }}>
          {lines.map((l, li) => <DarkLine key={li} text={l} />)}
        </div>
      )}
    </>
  );
}

// ===== 主组件 =====

export default function StepsPanel({ executionId, onReExecute }: StepsPanelProps) {
  const [activeTab, setActiveTab] = useState<"steps" | "history">("steps");
  const [steps, setSteps] = useState<ExecutionEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const historyRef = useRef<HistoryPanelHandle>(null);

  const handleHistoryReplay = useCallback(
    (newId: string) => {
      setActiveTab("steps");
      onReExecute(newId);
    },
    [onReExecute],
  );

  useEffect(() => {
    if (!executionId) return;
    setSteps([]);
    setLoading(true);
    const cancel = streamExecution(executionId, (event) => {
      setSteps((prev) => [...prev, event]);
      if (event.type === "done" || event.type === "error") setLoading(false);
    });
    return cancel;
  }, [executionId]);

  // 核心合并逻辑：按事件流分段，每步骤一张卡
  const cards = useMemo(() => {
    const list: StepCard[] = [];
    const byIndex = new Map<number, StepCard>();

    // 缓存当前步骤号，用于把 step_echo 归入正确步骤
    let curStepIdx = 0;
    // 缓存 step_start 之前的 pipeline 事件
    const pendingPipeline: ExecutionEvent[] = [];

    steps.forEach((ev, i) => {
      // 1) pipeline 事件（validate/auth/request/response/format 等，无 index）
      if (!ev.type.startsWith("step_") && ev.type !== "flow_end" && ev.type !== "done" && ev.type !== "error") {
        pendingPipeline.push(ev);
        return;
      }

      // 2) flow_end / done → 跳过（不纳入步骤卡片）
      if (ev.type === "flow_end" || ev.type === "done") {
        return;
      }

      // 3) step_start → 创建新卡片，pendingPipeline 归入此步骤
      if (ev.type === "step_start") {
        curStepIdx = Number(ev.index) || 0;
        const label = typeof ev.label === "string" && ev.label ? ` · ${ev.label}` : "";
        const title = curStepIdx > 0 ? `步骤 ${curStepIdx}${label}` : ev.type;
const card: StepCard = {
            key: `${i}-${curStepIdx}`,
            title,
            time: timeToDisplay(ev.time),
            status: "running",
            isDoneEvent: false,
            summaryLines: ev.use ? [`use: ${String(ev.use)}`] : [],
            paramsEntries: [],
            pipelineEvents: [...pendingPipeline],
            logs: [],
            formatLines: [],
            tableString: undefined,
          };
        pendingPipeline.length = 0; // 清空
        byIndex.set(curStepIdx, card);
        list.push(card);
        return;
      }

      // 4) step_done → 合并结果到已有卡片
      if (ev.type === "step_done") {
        const idx = Number(ev.index) || 0;
        const existing = byIndex.get(idx);
        if (existing) {
          existing.time = timeToDisplay(ev.time);
          existing.status = ev.status === "fail" ? "error" : "done";
          // use/elapsed → summaryLines
          const use = String(ev.use ?? "");
          if (use && !existing.summaryLines.some(l => l.startsWith("use:"))) {
            existing.summaryLines.unshift(`use: ${use}`);
          }
          if (ev.elapsed_ms !== undefined) {
            const durLine = `耗时 ${String(ev.elapsed_ms)}ms`;
            if (!existing.summaryLines.some(l => l.startsWith("耗时"))) {
              existing.summaryLines.push(durLine);
            }
          }
          // result_preview → summaryLines 底部 + 提取 table
          const rp = String(ev.result_preview ?? "");
          if (rp && rp !== "{}" && rp !== "null" && rp !== "undefined") {
            try {
              const parsed = JSON.parse(rp);
              if (typeof parsed === "object" && parsed !== null) {
                // 检测内嵌的 table 结构
                const tbl = parsed.table;
                if (tbl) {
                  if (typeof tbl === "string" && tbl.length > 20) {
                    // 文本表格（rich Table 渲染字符串）
                    existing.tableString = tbl;
                  } else if (typeof tbl === "object" && Array.isArray(tbl.columns) && tbl.columns.length > 0) {
                    // 结构化表格数据
                    existing.table = tbl as TableData;
                    existing.formatLines = [JSON.stringify(parsed, null, 2)];
                  }
                }
                // 拍平为非 table 的键值对
                existing.summaryLines = existing.summaryLines.filter(l => !l.startsWith("code:") && !l.startsWith("msg:"));
                for (const [k, v] of Object.entries(parsed)) {
                  if (k === "table") continue;
                  const vs = formatValue(v);
                  if (vs && vs !== "null" && vs !== "undefined") {
                    existing.summaryLines.push(`${k}: ${vs.length > 500 ? vs.slice(0, 500) + "…" : vs}`);
                  }
                }
              }
            } catch {
              // fallback: 非 JSON 时直接用原文本
              existing.summaryLines.push(rp);
            }
          }
          // params_preview → 参数详情
          const pp = String(ev.params_preview ?? "");
          if (pp && pp !== "{}" && pp !== "null" && pp !== "undefined") {
            existing.paramsEntries = parsePreviewToEntries(pp);
          }
        } else {
          // fallback: step_done 没有对应 step_start（理论上不应发生）
          const label = typeof ev.label === "string" && ev.label ? ` · ${ev.label}` : "";
          const title = idx > 0 ? `步骤 ${idx}${label}` : ev.type;
          const card: StepCard = {
            key: `${i}-${idx}`,
            title,
            time: timeToDisplay(ev.time),
            status: ev.status === "fail" ? "error" : "done",
            isDoneEvent: false,
            summaryLines: [],
            paramsEntries: [],
            pipelineEvents: [],
            logs: [],
            formatLines: [],
          };
          byIndex.set(idx, card);
          list.push(card);
        }
        return;
      }

      // 5) format → 收集 table 和 formatLines
      if (ev.type === "format") {
        // 归入当前最后一张卡片
        const lastCard = list[list.length - 1];
        if (lastCard && lastCard.status !== "done") {
          // 仍在运行中，追加
          if (ev.table && ev.table.columns.length > 0 && ev.table.rows.length > 0) {
            lastCard.table = ev.table;
          }
          const preview = typeof ev.output_preview === "string" ? ev.output_preview : formatValue(ev.output_preview);
          if (preview) {
            lastCard.formatLines = preview.split("\n");
          }
          // 也作为 pipeline 事件
          lastCard.pipelineEvents.push(ev);
        } else {
          pendingPipeline.push(ev);
        }
        return;
      }

      // 6) step_echo → 归入当前步骤的日志
      if (ev.type === "step_echo") {
        const msg = String(ev.message ?? "");
        if (msg) {
          const existing = byIndex.get(curStepIdx);
          if (existing) {
            existing.logs = [...existing.logs, cleanEcho(msg)];
          } else {
            // 兜底：放到最后一张卡片
            const lastCard = list[list.length - 1];
            if (lastCard) lastCard.logs = [...lastCard.logs, cleanEcho(msg)];
          }
        }
        return;
      }

      // 7) error → 跳过（由 done 事件处理）
      if (ev.type === "error") return;
    });

    return list;
  }, [steps, loading]);

  // 顶部 badge
  const doneSteps = steps.filter((s) => s.type === "step_done").length;
  const maxStepIndex = steps.reduce((m, s) => {
    const idx = Number(s.index);
    return (s.type === "step_start" || s.type === "step_done") && idx > m ? idx : m;
  }, 0);
  const flowEndCount = steps.find((s) => s.type === "flow_end")?.step_count;
  const flowTotal = flowEndCount !== undefined ? Number(flowEndCount) : maxStepIndex;
  const doneEvent = steps.find((s) => s.type === "done");
  const badge = flowTotal > 0
    ? `编排步骤 ${doneSteps}/${flowTotal}`
    : doneEvent
      ? `耗时 ${String(doneEvent.duration_ms)}ms`
      : loading
        ? "执行中…"
        : "";

  const copyText = cards.map((c) =>
    `[${c.time}] ${c.title}\n${c.summaryLines.map(l => `  ${l}`).join("\n")}`
  ).join("\n\n");

  const handleCopy = () => {
    if (!copyText) return;
    void navigator.clipboard?.writeText(copyText).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <section
      data-testid="right-panel"
      style={{ minWidth: 0, flex: 1, ...cardBase, display: "flex", flexDirection: "column", overflow: "hidden" }}
    >
      <style>{`@keyframes cliyard-breathe { 0%,100%{opacity:1} 50%{opacity:.3} }
.cliyard-table-row:hover { background-color: ${neutral[50]}; }`}</style>

      {/* tab bar */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: `1px solid ${neutral[200]}`, backgroundColor: neutral[50],
        padding: `0 ${space.sm}px`, borderRadius: `${radius.lg}px ${radius.lg}px 0 0`,
      }}>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          {[
            { id: "steps", label: "执行步骤" },
            { id: "history", label: "历史记录" },
          ].map((t) => (
            <button
              key={t.id} type="button" data-testid="panel-tab"
              data-active={activeTab === t.id ? "true" : "false"}
              onClick={() => setActiveTab(t.id as "steps" | "history")}
              style={{
                border: "none", background: "transparent", cursor: "pointer",
                padding: `${space.md}px ${space.lg}px`, marginBottom: -1,
                fontSize: fontSize.md, fontFamily: fontFamily.body,
                borderBottom: `2px solid ${activeTab === t.id ? brand[500] : "transparent"}`,
                color: activeTab === t.id ? brand[600] : neutral[500],
                fontWeight: activeTab === t.id ? 500 : 400,
                transition: "color .15s ease, border-color .15s ease",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        {activeTab === "steps" ? (
          <div style={{ display: "flex", alignItems: "center", gap: space.sm, paddingBottom: space.sm, paddingRight: space.sm }}>
            {badge && (
              <span data-testid="steps-badge" style={{
                borderRadius: radius.sm, backgroundColor: "#FFFFFF",
                padding: "2px 8px", fontFamily: fontFamily.mono,
                fontSize: fontSize.xs, color: neutral[500],
                border: `1px solid ${neutral[200]}`,
              }}>
                {badge}
              </span>
            )}
            <button type="button" className="cliyard-outline-btn" data-testid="re-run-button" onClick={() => onReExecute()}>
              重新执行
            </button>
            <button type="button" className="cliyard-text-btn" data-testid="copy-button" onClick={handleCopy}>
              {copied ? "已复制" : "复制"}
            </button>
            <button type="button" className="cliyard-text-btn" data-testid="clear-button" onClick={() => { setSteps([]); setLoading(false); }}>
              清空
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: space.sm, paddingBottom: space.sm, paddingRight: space.sm }}>
            <button type="button" className="cliyard-outline-btn" data-testid="clear-history-button" onClick={() => void historyRef.current?.clear()}>
              清空记录
            </button>
            <button type="button" className="cliyard-outline-btn" data-testid="refresh-history-button" onClick={() => historyRef.current?.reload()}>
              刷新
            </button>
          </div>
        )}
      </div>

      {activeTab === "steps" ? (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {cards.length === 0 ? (
            <EmptyState text={executionId ? "等待执行事件…" : "执行命令后此处显示步骤流"} />
          ) : (
            <ol style={{ display: "flex", flexDirection: "column", margin: 0, padding: space.lg, listStyle: "none" }}>
              {cards.map((c, i) => (
                <li key={c.key} style={{ position: "relative", display: "flex", gap: space.md, paddingBottom: space.lg }}>
                  {i !== cards.length - 1 && (
                    <span aria-hidden style={{ position: "absolute", left: 11.5, top: 26, bottom: 0, width: 1, backgroundColor: neutral[200] }} />
                  )}
                  <StepIcon status={c.status} isDoneEvent={c.isDoneEvent} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    {/* 标题行 */}
                    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: space.sm }}>
                      <span style={{ fontSize: fontSize.sm, fontWeight: 600, color: c.status === "error" ? statusColors.error.color : neutral[800] }}>
                        {c.title}
                      </span>
                      <span style={{
                        borderRadius: radius.sm, backgroundColor: neutral[100],
                        padding: "2px 6px", fontFamily: fontFamily.mono,
                        fontSize: fontSize.xs, color: neutral[500],
                      }}>
                        {c.time}
                      </span>
                      {c.status === "error" && (
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: space.xs,
                          padding: "1px 8px", borderRadius: radius.pill,
                          backgroundColor: statusColors.error.bg,
                          border: `1px solid ${statusColors.error.border}`,
                          color: statusColors.error.color, fontSize: fontSize.xs,
                          fontWeight: 500, ...baseFont,
                        }}>
                          失败
                        </span>
                      )}
                      {c.status === "running" && (
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: space.xs,
                          padding: "1px 8px", borderRadius: radius.pill,
                          backgroundColor: statusColors.running.bg,
                          border: `1px solid ${statusColors.running.border}`,
                          color: statusColors.running.color, fontSize: fontSize.xs,
                          fontWeight: 500, ...baseFont,
                        }}>
                          <span aria-hidden style={{
                            width: 5, height: 5, borderRadius: "50%",
                            backgroundColor: statusColors.running.color,
                            animation: "cliyard-breathe 1.2s ease-in-out infinite",
                          }} />
                          执行中
                        </span>
                      )}
                    </div>

                    {/* 始终可见的摘要行 */}
                    {c.summaryLines.length > 0 && (
                      <div style={{ marginTop: space.sm }}>
                        {c.summaryLines.map((l, li) => (
                          <div key={li} style={{
                            fontFamily: fontFamily.mono, fontSize: fontSize.xs,
                            lineHeight: 1.6, color: neutral[700],
                            whiteSpace: "pre-wrap", wordBreak: "break-all",
                          }}>
                            {l}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 表格（format 事件） */}
                    {c.table && (
                      <div style={{ marginTop: space.sm, borderRadius: radius.md, border: `1px solid ${neutral[200]}`, backgroundColor: "#FFFFFF" }}>
                        <FormatCardBody table={c.table} lines={c.formatLines} />
                      </div>
                    )}

                    {/* 文本表格（内嵌 table 字符串，如 rich Table 渲染结果） */}
                    {c.tableString && (
                      <div style={{ marginTop: space.sm, borderRadius: radius.md, border: `1px solid ${neutral[200]}`, backgroundColor: neutral[900], overflowX: "auto" }}>
                        <pre style={{
                          margin: 0, padding: space.sm,
                          fontFamily: fontFamily.mono, fontSize: fontSize.xs,
                          lineHeight: 1.4, color: "#6EE7B7",
                          whiteSpace: "pre", minWidth: "fit-content",
                        }}>
                          {c.tableString}
                        </pre>
                      </div>
                    )}

                    {/* 参数详情（默认收起） */}
                    {c.paramsEntries.length > 0 && (
                      <CollapsiblePanel title="参数详情" count={c.paramsEntries.length}>
                        {c.paramsEntries.map(([k, v], pi) => (
                          <KVPair key={pi} k={k} v={v} />
                        ))}
                      </CollapsiblePanel>
                    )}

                    {/* 请求详情（默认收起） */}
                    {c.pipelineEvents.length > 0 && (
                      <CollapsiblePanel title="请求详情" count={c.pipelineEvents.length}>
                        {c.pipelineEvents.map((pe, pi) => (
                          <DarkLine key={pi} text={pipelineToLine(pe)} dim={pe.type === "validate" || pe.type === "auth"} />
                        ))}
                      </CollapsiblePanel>
                    )}

                    {/* 日志（默认收起） */}
                    {c.logs.length > 0 && (
                      <CollapsiblePanel title="日志" count={c.logs.length}>
                        {c.logs.map((log, li) => (
                          <DarkLine key={li} text={log} />
                        ))}
                      </CollapsiblePanel>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <HistoryPanel ref={historyRef} onReExecute={handleHistoryReplay} />
        </div>
      )}
    </section>
  );
}
