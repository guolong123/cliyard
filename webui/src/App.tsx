import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import TopBar from "./components/TopBar";
import CommandTree from "./components/CommandTree";
import type { Selection } from "./components/CommandTree";
import CommandForm from "./components/CommandForm";
import type { CommandFormHandle } from "./components/CommandForm";
import StepsPanel from "./components/StepsPanel";
import AuthPanel from "./components/AuthPanel";
import { execute, fetchSpec } from "./api/client";
import type { SpecData } from "./api/client";
import { neutral, space, radius, fontSize, fontFamily, shadow, statusColors } from "./styles/tokens";

const baseFont: CSSProperties = { fontFamily: fontFamily.body };

/** 卡片外壳：白底 + 1px 边框 + 轻阴影 + 大圆角（对齐原型 cardBase） */
const cardBase: CSSProperties = {
  backgroundColor: "#FFFFFF",
  border: `1px solid ${neutral[200]}`,
  borderRadius: radius.lg,
  boxShadow: shadow.sm,
};

/** 从 spec 查选中命令/flow 的 JSON Schema（command: group.method；flow: command 匹配） */
function schemaForSelection(spec: SpecData | null, selected: Selection | null): Record<string, unknown> | null {
  if (!spec || !selected) return null;
  if (selected.kind === "command") {
    const [groupName, methodName] = selected.target.split(".");
    for (const g of spec.groups) {
      // 扁平组：group 名即资源名
      if (g.group === groupName) {
        const c = g.commands.find((c) => c.name === methodName);
        if (c) return c.schema ?? null;
      }
      // 两级组：资源名匹配（target = 资源名.方法名）
      const r = g.resources?.find((r) => r.name === groupName);
      if (r) {
        const c = r.commands.find((c) => c.name === methodName);
        if (c) return c.schema ?? null;
      }
    }
    return null;
  }
  const flow = spec.flows.find((f) => f.command === selected.target);
  return flow?.params_schema ?? null;
}

/** 最近一次执行（供右侧「重新执行」复用 params） */
interface LastRun {
  kind: "command" | "flow";
  target: string;
  params: Record<string, unknown>;
}

/**
 * 应用外壳：顶栏 + 三栏
 * 左 320px 命令树 / 中 320px rjsf 表单 / 右 flex-1 执行步骤（SSE）+ 历史占位
 */
export default function App() {
  const [spec, setSpec] = useState<SpecData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<LastRun | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const formRef = useRef<CommandFormHandle>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSpec()
      .then((data) => {
        if (!cancelled) setSpec(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedSchema = useMemo(() => schemaForSelection(spec, selected), [spec, selected]);

  const handleExecute = useCallback(
    (id: string, params?: Record<string, unknown>) => {
      if (!selected) return;
      setLastRun({ kind: selected.kind, target: selected.target, params: params ?? {} });
      setExecutionId(id);
    },
    [selected],
  );

  /** 重新执行（id 来自历史重放时直接订阅；否则复用 lastRun params 重新提交） */
  const handleReExecute = useCallback((id?: string) => {
    if (id) {
      setExecutionId(id);
      return;
    }
    if (!lastRun) return;
    void execute(lastRun.kind, lastRun.target, lastRun.params).then(({ execution_id }) =>
      setExecutionId(execution_id),
    );
  }, [lastRun]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        backgroundColor: neutral[50],
        ...baseFont,
      }}
    >
      <TopBar
          service={spec?.service}
          onAuthClick={() => setAuthOpen(true)}
        />
      <AuthPanel open={authOpen} onClose={() => setAuthOpen(false)} />

      {/* 内容区：三栏 */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: space.lg, padding: space.xl }}>
        {/* ① 命令树 / 收藏夹 */}
        <aside
          data-testid="command-tree"
          style={{ width: 320, flexShrink: 0, ...cardBase, padding: space.lg, overflowY: "auto" }}
        >
          {loadError ? (
            <div style={{ fontSize: fontSize.xs, color: statusColors.error.color, ...baseFont }}>
              加载失败：{loadError}
            </div>
          ) : spec ? (
            <CommandTree spec={spec} selected={selected} onSelect={setSelected} />
          ) : (
            <div style={{ fontSize: fontSize.xs, color: neutral[400], ...baseFont }}>加载中…</div>
          )}
        </aside>

        {/* ② 命令表单 */}
        {selected ? (
          <CommandForm
            ref={formRef}
            kind={selected.kind}
            target={selected.target}
            schema={selectedSchema}
            onExecute={handleExecute}
          />
        ) : (
          <section
            data-testid="command-form"
            style={{ width: 320, flexShrink: 0, ...cardBase, padding: space.lg, ...baseFont }}
          >
            <div style={{ fontSize: fontSize.sm, color: neutral[400] }}>选择左侧命令或 flow 开始</div>
          </section>
        )}

        {/* ③ 执行步骤 / 历史 */}
        <StepsPanel executionId={executionId} onReExecute={handleReExecute} />
      </div>
    </div>
  );
}
