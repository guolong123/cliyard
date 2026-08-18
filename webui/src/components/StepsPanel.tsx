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

/** 事件类型 → 中文标题 */
const EVENT_TITLES: Record<string, string> = {
  validate: "参数校验",
  auth: "认证准备",
  request: "发送请求",
  response: "等待响应",
  format: "格式化结果",
  done: "完成",
  error: "错误",
  step_start: "步骤开始",
  step_done: "步骤结束",
  flow_end: "流程结束",
  step_echo: "消息",
};

type StepStatus = "done" | "running" | "error";

interface StepCard {
  key: string;
  title: string;
  time: string;
  status: StepStatus;
  isDoneEvent: boolean;
  lines: string[];
  mono: boolean;
  /** format 事件的结构化表格数据（有 table 时渲染表格视图，可切换 JSON） */
  table?: TableData;
}

/** 值渲染：对象/数组 → JSON 单行，其他 → 原样 */
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

/** ISO 时间 → "HH:MM:SS.mmm"（对齐原型步骤时间样式） */
function timeToDisplay(iso: string): string {
  return iso.length >= 23 ? iso.slice(11, 23) : iso;
}

/** 事件 payload → 内容行（对齐原型步骤 body） */
function payloadToLines(event: ExecutionEvent): { lines: string[]; mono: boolean } {
  switch (event.type) {
    case "validate": {
      const params = event.params as Record<string, Record<string, unknown>> | undefined;
      if (!params || typeof params !== "object") return { lines: [], mono: false };
      const rows = Object.entries(params).flatMap(([loc, kv]) =>
        kv && typeof kv === "object"
          ? Object.entries(kv).map(([k, v]) => `${loc}.${k} = ${formatValue(v)}`)
          : [],
      );
      return { lines: [`已绑定 ${rows.length} 个参数：`, ...rows.map((l) => `  ${l}`)], mono: false };
    }
    case "auth": {
      const mode = formatValue(event.mode) || "chain";
      const prefilled = Array.isArray(event.pre_filled_keys)
        ? (event.pre_filled_keys as string[]).join(", ")
        : "";
      return { lines: [`认证模式: ${mode}${prefilled ? ` · 预填: ${prefilled}` : ""}`], mono: false };
    }
    case "request": {
      const lines = [`${String(event.method ?? "GET")} ${String(event.url ?? "")}`];
      const headers = event.headers as Record<string, unknown> | undefined;
      if (headers && typeof headers === "object" && Object.keys(headers).length) {
        lines.push(...Object.entries(headers).map(([k, v]) => `${k}: ${formatValue(v)}`));
      }
      const qp = event.query_params as Record<string, unknown> | undefined;
      if (qp && typeof qp === "object" && Object.keys(qp).length) {
        lines.push(`query: ${JSON.stringify(qp)}`);
      }
      if (event.body !== undefined && event.body !== null && event.body !== "") {
        lines.push(`body: ${formatValue(event.body)}`);
      }
      return { lines, mono: true };
    }
    case "response": {
      return { lines: [`HTTP ${String(event.status_code ?? "?")} · ${String(event.elapsed_ms ?? "?")}ms`], mono: false };
    }
    case "format": {
      const preview =
        typeof event.output_preview === "string"
          ? event.output_preview
          : formatValue(event.output_preview);
      return { lines: preview.split("\n"), mono: true };
    }
    case "step_echo": {
      return { lines: [String(event.message ?? "")], mono: false };
    }
    case "done": {
      return { lines: [`status = ${String(event.status)} · 耗时 ${String(event.duration_ms)}ms`], mono: false };
    }
    case "error": {
      return { lines: [String(event.message ?? "执行失败")], mono: false };
    }
    case "step_start": {
      const use = formatValue(event.use);
      return { lines: use ? [`use: ${use}`] : [], mono: false };
    }
    case "step_done": {
      const lines: string[] = [];
      const use = formatValue(event.use);
      if (use) lines.push(`use: ${use}`);
      if (event.elapsed_ms !== undefined) lines.push(`elapsed = ${String(event.elapsed_ms)}ms`);
      const preview = formatValue(event.result_preview);
      if (preview && preview !== "null" && preview !== "undefined") lines.push(preview);
      return { lines, mono: true };
    }
    case "flow_end": {
      return { lines: [`outcome = ${String(event.outcome)} · ${String(event.step_count)} 个步骤`], mono: false };
    }
    default: {
      const rows = Object.entries(event)
        .filter(([k]) => k !== "type" && k !== "time")
        .map(([k, v]) => `${k} = ${formatValue(v)}`);
      return { lines: rows, mono: false };
    }
  }
}

/** 事件 → 步骤卡片（step_start/step_done 标题带序号，flow_end 等用中文映射） */
function eventToCard(event: ExecutionEvent, index: number, isLast: boolean, loading: boolean): StepCard {
  const stepIdx = event.index !== undefined ? Number(event.index) : 0;
  const label = typeof event.label === "string" && event.label ? ` · ${event.label}` : "";
  const title =
    (event.type === "step_start" || event.type === "step_done") && stepIdx > 0
      ? `步骤 ${stepIdx}${label}`
      : (EVENT_TITLES[event.type] ?? event.type);
  const { lines, mono } = payloadToLines(event);
  const status: StepStatus = event.type === "error" ? "error" : isLast && loading ? "running" : "done";
  const table =
    event.type === "format" && event.table && event.table.columns.length > 0 && event.table.rows.length > 0
      ? event.table
      : undefined;
  return {
    key: `${index}-${event.type}`,
    title,
    time: timeToDisplay(event.time),
    status,
    isDoneEvent: event.type === "done",
    lines,
    mono,
    table,
  };
}

/** 深色代码块行级语法着色（对齐原型 MonoLine：方法琥珀/URL 天蓝/JSON 键天蓝/字符串翠绿/数字琥珀） */
function MonoLine({ line }: { line: string }) {
  const reqM = line.match(/^([A-Z]{3,6})\s+(https?:\/\/\S+)(.*)$/);
  if (reqM)
    return (
      <>
        <span style={{ fontWeight: 600, color: "#FBBF24" }}>{reqM[1]}</span>
        <span style={{ color: "#7DD3FC" }}> {reqM[2]}</span>
        <span style={{ color: "#94A3B8" }}>{reqM[3]}</span>
      </>
    );
  const kvM = line.match(/^(\s*"[\w-]+"\s*:\s*)(.*?)(,?)$/);
  if (kvM) {
    const val = kvM[2];
    const valColor = val.startsWith('"')
      ? "#6EE7B7"
      : /^-?\d/.test(val)
        ? "#FBBF24"
        : val.includes("{") || val.includes("[")
          ? "#7DD3FC"
          : "#C4B5FD";
    return (
      <>
        <span style={{ color: "#7DD3FC" }}>{kvM[1]}</span>
        <span style={{ color: valColor }}>{kvM[2]}</span>
        <span style={{ color: "#64748B" }}>{kvM[3]}</span>
      </>
    );
  }
  const headM = line.match(/^([\w-]+):\s+(.*)$/);
  if (headM)
    return (
      <>
        <span style={{ color: "#94A3B8" }}>{headM[1]}:</span>
        <span style={{ color: "#E2E8F0" }}> {headM[2]}</span>
      </>
    );
  return <>{line}</>;
}

/** 内容行渲染：mono=深色代码块（语法着色）/ 普通=浅色块（步骤说明） */
function MonoBlock({ lines, mono }: { lines: string[]; mono: boolean }) {
  if (mono)
    return (
      <pre
        style={{
          margin: 0,
          overflowX: "auto",
          overflowY: "auto",
          backgroundColor: neutral[900],
          padding: `${space.sm + 2}px ${space.md}px`,
          fontFamily: fontFamily.mono,
          fontSize: fontSize.xs,
          lineHeight: 1.7,
          color: "#6EE7B7",
        }}
      >
        {lines.map((line, li) => (
          <span key={li} style={{ display: "block", whiteSpace: "pre" }}>
            <span
              style={{
                display: "inline-block",
                width: 16,
                marginRight: space.md,
                textAlign: "right",
                userSelect: "none",
                color: neutral[600],
              }}
            >
              {li + 1}
            </span>
            <MonoLine line={line} />
          </span>
        ))}
      </pre>
    );
  return (
    <pre style={{ margin: 0, overflowX: "auto", overflowY: "auto", backgroundColor: neutral[50], padding: `${space.sm + 2}px ${space.md}px`, fontSize: fontSize.xs, lineHeight: 1.7, ...baseFont }}>
      {lines.map((line, li) => (
        <span
          key={li}
          style={{
            display: "block",
            whiteSpace: "pre",
            color: line.startsWith(" ") ? neutral[500] : neutral[700],
            fontWeight: line.startsWith(" ") ? 400 : 500,
          }}
        >
          {line || "\u00A0"}
        </span>
      ))}
    </pre>
  );
}

/** 结果表格：白底 + neutral-200 边框圆角，表头 alias，数据行 hover 高亮，溢出横向滚动 */
function ResultTable({ columns, rows }: { columns: TableColumn[]; rows: string[][] }) {
  return (
    <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: fontFamily.mono, fontSize: fontSize.xs }}>
          <thead>
            <tr style={{ backgroundColor: neutral[50] }}>
              {columns.map((c, ci) => (
                <th
                  key={ci}
                  style={{
                    textAlign: "left",
                    padding: `${space.sm}px ${space.md}px`,
                    borderBottom: `1px solid ${neutral[200]}`,
                    fontWeight: 600,
                    color: neutral[700],
                    whiteSpace: "nowrap",
                  }}
                >
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

/** 格式化结果卡片主体：有 table 时表格/JSON 可切换（默认表格），JSON 回退到深色代码块 */
function FormatCardBody({ table, lines }: { table: TableData; lines: string[] }) {
  const [view, setView] = useState<"table" | "json">("table");
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${space.xs}px ${space.sm}px`,
          borderBottom: `1px solid ${neutral[200]}`,
          backgroundColor: neutral[50],
        }}
      >
        <span style={{ fontSize: fontSize.xs, color: neutral[500], ...baseFont }}>
          {table.total !== undefined ? `共 ${String(table.total)} 条` : "表格视图"}
        </span>
        <div style={{ display: "flex", gap: space.xs }}>
          {(["table", "json"] as const).map((v) => (
            <button
              key={v}
              type="button"
              data-testid={`format-view-${v}`}
              data-active={view === v ? "true" : "false"}
              onClick={() => setView(v)}
              className="cliyard-text-btn"
              style={{
                padding: "1px 8px",
                fontSize: fontSize.xs,
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
        <MonoBlock lines={lines} mono />
      )}
    </>
  );
}

/** 步骤状态图标：成功=绿实心圆勾 / 完成（done）=品牌蓝实心圆勾 / 失败=红× / 运行中=呼吸蓝点 */
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
            width: 6,
            height: 6,
            borderRadius: "50%",
            backgroundColor: brand[500],
            animation: "cliyard-breathe 1.2s ease-in-out infinite",
          }}
        />
      </span>
    );
  return (
    <span
      data-testid="step-icon"
      data-status="done"
      style={{
        ...base,
        backgroundColor: isDoneEvent ? brand[500] : statusColors.success.color,
        color: "#FFFFFF",
      }}
    >
      <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    </span>
  );
}

/** 空态占位 */
function EmptyState({ text }: { text: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: 160,
        margin: space.lg,
        borderRadius: radius.md,
        border: `1px dashed ${neutral[200]}`,
        backgroundColor: neutral[50],
        color: neutral[400],
        fontSize: fontSize.sm,
        ...baseFont,
      }}
    >
      {text}
    </div>
  );
}

/**
 * 右侧执行步骤面板：SSE 事件流 → 步骤时间线。
 * executionId 变化时重新订阅（done/error 自动收尾），卸载时断开。
 */
export default function StepsPanel({ executionId, onReExecute }: StepsPanelProps) {
  const [activeTab, setActiveTab] = useState<"steps" | "history">("steps");
  const [steps, setSteps] = useState<ExecutionEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const historyRef = useRef<HistoryPanelHandle>(null);

  // 历史重放成功：切回「执行步骤」tab 并让父级订阅新 execution_id
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

  // flow 步骤卡片按 index 合并 step_start/step_done：同一步骤只渲染一张卡，
  // step_start 建立卡片（use 行 + running），step_done 追加结果行并置为 done/error。
  const cards = useMemo(() => {
    const list: StepCard[] = [];
    const byIndex = new Map<number, StepCard>();
    const lastIdx = steps.length - 1;
    steps.forEach((ev, i) => {
      if (ev.type === "step_start") {
        const card = eventToCard(ev, i, i === lastIdx, loading);
        card.status = "running";
        byIndex.set(Number(ev.index) || 0, card);
        list.push(card);
      } else if (ev.type === "step_done") {
        const existing = byIndex.get(Number(ev.index) || 0);
        if (existing) {
          const { lines, mono } = payloadToLines(ev);
          existing.lines = existing.lines.concat(
            lines.filter((l) => !existing.lines.includes(l)),
          );
          if (mono) existing.mono = true;
          existing.time = timeToDisplay(ev.time);
          existing.status = ev.status === "fail" ? "error" : "done";
        } else {
          list.push(eventToCard(ev, i, i === lastIdx, loading));
        }
      } else {
        list.push(eventToCard(ev, i, i === lastIdx, loading));
      }
    });
    return list;
  }, [steps, loading]);

  // 顶部 badge：flow 编排步骤进度 / 命令耗时
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

  const copyText = cards
    .map((c) => `[${c.time}] ${c.title}\n${c.lines.map((l) => `  ${l}`).join("\n")}`)
    .join("\n\n");

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
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: `1px solid ${neutral[200]}`,
          backgroundColor: neutral[50],
          padding: `0 ${space.sm}px`,
          borderRadius: `${radius.lg}px ${radius.lg}px 0 0`,
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          {(
            [
              { id: "steps", label: "执行步骤" },
              { id: "history", label: "历史记录" },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              data-testid="panel-tab"
              data-active={activeTab === t.id ? "true" : "false"}
              onClick={() => setActiveTab(t.id)}
              style={{
                border: "none",
                background: "transparent",
                cursor: "pointer",
                padding: `${space.md}px ${space.lg}px`,
                marginBottom: -1,
                fontSize: fontSize.md,
                fontFamily: fontFamily.body,
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
              <span
                data-testid="steps-badge"
                style={{
                  borderRadius: radius.sm,
                  backgroundColor: "#FFFFFF",
                  padding: "2px 8px",
                  fontFamily: fontFamily.mono,
                  fontSize: fontSize.xs,
                  color: neutral[500],
                  border: `1px solid ${neutral[200]}`,
                }}
              >
                {badge}
              </span>
            )}
            <button type="button" className="cliyard-outline-btn" data-testid="re-run-button" onClick={() => onReExecute()}>
              重新执行
            </button>
            <button type="button" className="cliyard-text-btn" data-testid="copy-button" onClick={handleCopy}>
              {copied ? "已复制" : "复制"}
            </button>
            <button
              type="button"
              className="cliyard-text-btn"
              data-testid="clear-button"
              onClick={() => {
                setSteps([]);
                setLoading(false);
              }}
            >
              清空
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: space.sm, paddingBottom: space.sm, paddingRight: space.sm }}>
            <button
              type="button"
              className="cliyard-outline-btn"
              data-testid="clear-history-button"
              onClick={() => void historyRef.current?.clear()}
            >
              清空记录
            </button>
            <button
              type="button"
              className="cliyard-outline-btn"
              data-testid="refresh-history-button"
              onClick={() => historyRef.current?.reload()}
            >
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
                    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: space.sm }}>
                      <span style={{ fontSize: fontSize.sm, fontWeight: 600, color: c.status === "error" ? statusColors.error.color : neutral[800] }}>
                        {c.title}
                      </span>
                      <span
                        style={{
                          borderRadius: radius.sm,
                          backgroundColor: neutral[100],
                          padding: "2px 6px",
                          fontFamily: fontFamily.mono,
                          fontSize: fontSize.xs,
                          color: neutral[500],
                        }}
                      >
                        {c.time}
                      </span>
                      {c.status === "error" && (
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: space.xs,
                            padding: "1px 8px",
                            borderRadius: radius.pill,
                            backgroundColor: statusColors.error.bg,
                            border: `1px solid ${statusColors.error.border}`,
                            color: statusColors.error.color,
                            fontSize: fontSize.xs,
                            fontWeight: 500,
                            ...baseFont,
                          }}
                        >
                          失败
                        </span>
                      )}
                      {c.status === "running" && (
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: space.xs,
                            padding: "1px 8px",
                            borderRadius: radius.pill,
                            backgroundColor: statusColors.running.bg,
                            border: `1px solid ${statusColors.running.border}`,
                            color: statusColors.running.color,
                            fontSize: fontSize.xs,
                            fontWeight: 500,
                            ...baseFont,
                          }}
                        >
                          <span
                            aria-hidden
                            style={{
                              width: 5,
                              height: 5,
                              borderRadius: "50%",
                              backgroundColor: statusColors.running.color,
                              animation: "cliyard-breathe 1.2s ease-in-out infinite",
                            }}
                          />
                          执行中
                        </span>
                      )}
                    </div>
                    {(c.lines.length > 0 || c.table) && (
                      <div style={{ marginTop: space.sm, overflowY: "auto", maxHeight: 400, borderRadius: radius.md, border: `1px solid ${neutral[200]}`, backgroundColor: "#FFFFFF" }}>
                        {c.table ? <FormatCardBody table={c.table} lines={c.lines} /> : <MonoBlock lines={c.lines} mono={c.mono} />}
                      </div>
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
