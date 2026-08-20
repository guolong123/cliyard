import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  brand,
  neutral,
  space,
  radius,
  fontSize,
  fontFamily,
  statusColors,
  type StatusTheme,
} from "../styles/tokens";
import type { Flow, GroupResource, SpecData, TreeItem } from "../api/client";

const baseFont: CSSProperties = { fontFamily: fontFamily.body };

export type SideTab = "commands" | "flows";

/** 选中项：命令 = {kind:"command", target:"resource.method"}；flow = {kind:"flow", target: flow.command} */
export interface Selection {
  kind: "command" | "flow";
  target: string;
}

interface CommandTreeProps {
  spec: SpecData;
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
}

/** labels pill 语义色：已调试→绿（success）、v2→蓝（brand）、其他→灰 */
function labelBadgeTheme(label: string): StatusTheme {
  if (label === "已调试") return statusColors.success;
  if (label === "v2") return { bg: brand[50], color: brand[600], border: brand[200] };
  return { bg: neutral[100], color: neutral[500], border: neutral[200] };
}

/** flow 参数个数（params_schema.properties 的键数） */
function flowParamCount(flow: Flow): number {
  const props = flow.params_schema?.properties;
  return props && typeof props === "object" ? Object.keys(props).length : 0;
}

/** category 标签由后端 API 通过 category_label 透传，前端不再硬编码 */

/** 树项/flow 项的 hover 与选中样式（token 值注入，前缀 cliyard- 避免污染） */
const treeCss = `
  .cliyard-tree-item {
    position: relative; display: flex; flex-direction: column; gap: 2px;
    width: 100%; padding: ${space.sm - 2}px ${space.md}px ${space.sm - 2}px ${space.md + 4}px;
    border: none; border-radius: ${radius.md}px; cursor: pointer; text-align: left;
    background-color: transparent; color: ${neutral[600]};
    font-size: ${fontSize.sm}px; font-family: ${fontFamily.mono};
    transition: background-color .15s ease, color .15s ease;
  }
  .cliyard-tree-item:hover { background-color: ${neutral[100]}; color: ${neutral[900]}; }
  .cliyard-tree-item[data-active="true"] { background-color: ${brand[50]}; color: ${brand[600]}; font-weight: 500; }
  .cliyard-tree-item[data-active="true"]:hover { background-color: ${brand[50]}; color: ${brand[600]}; }

  .cliyard-group-header {
    border-radius: ${radius.sm}px;
    transition: background-color .15s ease;
  }
  .cliyard-group-header:hover { background-color: ${neutral[100]}; }

  .cliyard-flow-item {
    position: relative; display: flex; flex-direction: column; gap: 2px;
    width: 100%; padding: ${space.sm}px ${space.md}px ${space.sm}px ${space.md + 4}px;
    border: none; border-radius: ${radius.md}px; cursor: pointer; text-align: left;
    background-color: transparent; color: ${neutral[600]};
    font-size: ${fontSize.sm}px; font-family: ${fontFamily.body};
    transition: background-color .15s ease, color .15s ease;
  }
  .cliyard-flow-item:hover { background-color: ${neutral[100]}; }
  .cliyard-flow-item[data-active="true"] { background-color: ${brand[50]}; }
  .cliyard-flow-item[data-active="true"] .cliyard-flow-name { color: ${brand[600]}; font-weight: 500; }
  .cliyard-flow-item[data-active="true"] .cliyard-flow-command { color: ${brand[500]}; }
`;

/** 选中指示条：左侧 3px 品牌蓝竖条（命令项/flow 项共用） */
function ActiveBar({ top }: { top: number | string }) {
  return (
    <span
      aria-hidden
      style={{
        position: "absolute",
        left: 0,
        top,
        transform: top === "50%" ? "translateY(-50%)" : undefined,
        width: 3,
        height: 18,
        borderRadius: radius.pill,
        backgroundColor: brand[500],
      }}
    />
  );
}

/** labels pill（纯色语义：已调试绿 / v2 蓝 / 其他灰） */
function LabelPill({ label }: { label: string }) {
  const t = labelBadgeTheme(label);
  return (
    <span
      style={{
        marginLeft: "auto",
        borderRadius: radius.pill,
        padding: "0 6px",
        backgroundColor: t.bg,
        border: `1px solid ${t.border}`,
        color: t.color,
        fontSize: 9,
        fontWeight: 600,
        lineHeight: "16px",
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

/** 空态占位：无命令 / 无 flow */
function EmptyState({ text }: { text: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: 96,
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
 * 左侧命令树 / flow 列表（对齐原型 command-panel ① 区域）：
 * 「命令 | Flow」tab（选中态 2px 品牌蓝下划线）+ 搜索框 + 分组渲染。
 * 命令项：mono 名称 + labels pill + 两行描述；Flow 项：mono 名称 + flow pill + 参数数 + 命令 + 两行描述。
 */
export default function CommandTree({ spec, selected, onSelect }: CommandTreeProps) {
  const [sideTab, setSideTab] = useState<SideTab>("commands");
  const [search, setSearch] = useState("");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => {
    return new Set(spec.groups.map((_, i) => `${spec.groups[i].group}-${i}`));
  });
  // flow 组默认折叠
  const [expandedFlowGroups, setExpandedFlowGroups] = useState<Set<string>>(
    () => new Set(),
  );

  const q = search.trim().toLowerCase();

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  // 搜索时自动展开所有匹配组
  const effectiveExpanded = useMemo(() => {
    if (q === "") return expandedGroups;
    const all = new Set<string>();
    spec.groups.forEach((g, i) => {
      const key = `${g.group}-${i}`;
      const groupHit =
        g.group.toLowerCase().includes(q) || g.desc.toLowerCase().includes(q);
      if (g.resources && g.resources.length > 0) {
        const resourceHit = g.resources.some(
          (r) =>
            r.name.toLowerCase().includes(q) ||
            r.desc.toLowerCase().includes(q) ||
            r.commands.some(
              (c) =>
                `${r.name}.${c.name}`.toLowerCase().includes(q) ||
                c.desc.toLowerCase().includes(q),
            ),
        );
        if (groupHit || resourceHit) all.add(key);
      } else {
        const commandHit = g.commands.some(
          (c) =>
            `${g.group}.${c.name}`.toLowerCase().includes(q) ||
            c.desc.toLowerCase().includes(q),
        );
        if (groupHit || commandHit) all.add(key);
      }
    });
    return all;
  }, [expandedGroups, q, spec.groups]);

  // 命令搜索：group.group/desc + resources 内子资源名/命令名/desc；flow 搜索：name/description/command
  const filteredGroups = useMemo(() => {
    return spec.groups
      .map((g) => {
        const groupHit =
          q === "" || g.group.toLowerCase().includes(q) || g.desc.toLowerCase().includes(q);
        if (g.resources && g.resources.length > 0) {
          const resources = g.resources
            .map((r) => ({
              ...r,
              commands: groupHit
                ? r.commands
                : r.commands.filter(
                    (c) =>
                      `${r.name}.${c.name}`.toLowerCase().includes(q) ||
                      c.desc.toLowerCase().includes(q),
                  ),
            }))
            .filter((r) => r.commands.length > 0);
          return { ...g, resources };
        }
        const commands = groupHit
          ? g.commands
          : g.commands.filter(
              (c) =>
                `${g.group}.${c.name}`.toLowerCase().includes(q) ||
                c.desc.toLowerCase().includes(q),
            );
        return { ...g, commands };
      })
      .filter(
        (g) => (g.resources && g.resources.length > 0) || g.commands.length > 0,
      );
  }, [spec.groups, q]);
  // flow 搜索：name/description/command + category 英文名/中文名
  const filteredFlows = useMemo(
    () =>
      spec.flows.filter((f) => {
        const cat = f.category || "";
        const catZh = f.category_label || "";
        return (
          f.name.toLowerCase().includes(q) ||
          f.description.toLowerCase().includes(q) ||
          f.command.toLowerCase().includes(q) ||
          cat.toLowerCase().includes(q) ||
          catZh.toLowerCase().includes(q)
        );
      }),
    [spec.flows, q],
  );

  /** 按 category 分组的 flows：[[category, flows], ...]；未设置 category 归入"其他" */
  const groupedFlows = useMemo(() => {
    const groups: Record<string, Flow[]> = {};
    for (const f of filteredFlows) {
      const cat = f.category || "其他";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(f);
    }
    return Object.entries(groups);
  }, [filteredFlows]);

  // 搜索时自动展开所有匹配到的 flow 组；无搜索词时尊重用户折叠状态
  const searchActive = q !== "";
  const effectiveExpandedFlowGroups = useMemo(() => {
    if (!searchActive) return expandedFlowGroups;
    const all = new Set<string>();
    for (const [cat] of groupedFlows) all.add(cat);
    return all;
  }, [expandedFlowGroups, groupedFlows, searchActive]);

  /** 命令项按钮（target = 资源名.方法名，与 executor 的 resource.method 语义一致） */
  const renderCommandItem = (c: TreeItem, targetPrefix: string) => {
    const target = `${targetPrefix}.${c.name}`;
    const on = selected?.kind === "command" && selected.target === target;
    return (
      <button
        key={target}
        type="button"
        data-testid="tree-item"
        data-active={on ? "true" : "false"}
        onClick={() => onSelect({ kind: "command", target })}
        className="cliyard-tree-item"
      >
        {on && <ActiveBar top={14} />}
        {/* 主行：mono 名称 + labels pill */}
        <span style={{ display: "flex", alignItems: "center", gap: space.sm, minWidth: 0 }}>
          <span
            style={{
              fontFamily: fontFamily.mono,
              fontSize: fontSize.sm,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {c.name}
          </span>
          {c.labels.map((lb) => (
            <LabelPill key={lb} label={lb} />
          ))}
        </span>
        {/* 次行：描述（两行内省略） */}
        {c.desc && (
          <span
            style={{
              fontSize: fontSize.xs,
              color: neutral[500],
              lineHeight: 1.5,
              overflow: "hidden",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {c.desc}
          </span>
        )}
      </button>
    );
  };

  /** 子资源小节标题：mono 资源名 + 灰字描述 */
  const renderResourceHeader = (r: GroupResource) => (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: space.sm,
        padding: `0 ${space.xs}px`,
        marginBottom: 2,
      }}
    >
      <span
        style={{
          fontFamily: fontFamily.mono,
          fontSize: fontSize.xs,
          fontWeight: 600,
          color: neutral[600],
        }}
      >
        {r.name}
      </span>
      {r.desc && (
        <span style={{ fontSize: fontSize.xs, color: neutral[600] }}>{r.desc}</span>
      )}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <style>{treeCss}</style>

      {/* 左侧 tab：命令 | Flow（选中态 2px 品牌蓝下划线，对齐右侧 tab 风格） */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          borderBottom: `1px solid ${neutral[200]}`,
          marginBottom: space.md,
        }}
      >
        {(
          [
            { id: "commands", label: "命令" },
            { id: "flows", label: "Flow" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid="side-tab"
            data-active={sideTab === t.id ? "true" : "false"}
            onClick={() => setSideTab(t.id)}
            style={{
              border: "none",
              background: "transparent",
              cursor: "pointer",
              padding: `${space.sm}px ${space.md}px`,
              marginBottom: -1,
              fontSize: fontSize.md,
              fontFamily: fontFamily.body,
              borderBottom: `2px solid ${sideTab === t.id ? brand[500] : "transparent"}`,
              color: sideTab === t.id ? brand[600] : neutral[500],
              fontWeight: sideTab === t.id ? 500 : 400,
              transition: "color .15s ease, border-color .15s ease",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 搜索（过滤当前 tab 内容） */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: space.sm,
          padding: `${space.sm}px ${space.md}px`,
          borderRadius: radius.md,
          backgroundColor: neutral[50],
          border: `1px solid ${neutral[200]}`,
          color: neutral[400],
          fontSize: fontSize.sm,
          marginBottom: space.lg,
          ...baseFont,
        }}
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          width={14}
          height={14}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          data-testid="tree-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={sideTab === "commands" ? "搜索命令…" : "搜索 flow…"}
          style={{
            flex: 1,
            minWidth: 0,
            border: "none",
            outline: "none",
            background: "transparent",
            fontFamily: fontFamily.body,
            fontSize: fontSize.sm,
            color: neutral[700],
          }}
        />
      </div>

      {sideTab === "commands" ? (
        filteredGroups.length === 0 ? (
          <EmptyState text="无命令" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
            {filteredGroups.map((g, i) => {
              const key = `${g.group}-${i}`;
              const expanded = effectiveExpanded.has(key);
              return (
                <div key={key}>
                  <button
                    type="button"
                    data-testid="group-header"
                    className="cliyard-group-header"
                    onClick={() => toggleGroup(key)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: space.xs,
                      width: "100%",
                      padding: `0 ${space.xs}px`,
                      marginBottom: space.sm,
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      textAlign: "left",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-flex",
                        width: 12,
                        height: 12,
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                        transition: "transform .15s ease",
                        transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
                        color: neutral[500],
                      }}
                    >
                      <svg viewBox="0 0 8 8" width={6} height={6} fill="currentColor">
                        <path d="M1.5 0L6.5 4L1.5 8z" />
                      </svg>
                    </span>
                    <span
                      style={{
                        flex: 1,
                        fontSize: fontSize.sm,
                        fontWeight: 600,
                        textTransform: "uppercase",
                        letterSpacing: 0.06,
                        color: neutral[700],
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {g.group}
                    </span>
                    {g.desc && (
                      <span
                        style={{
                          fontSize: fontSize.xs,
                          color: neutral[500],
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: "40%",
                        }}
                      >
                        {g.desc}
                      </span>
                    )}
                  </button>
                  {expanded &&
                    (g.resources && g.resources.length > 0 ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: space.md }}>
                        {g.resources.map((r, ri) => (
                          <div key={`${g.group}-${r.name}-${ri}`}>
                            {renderResourceHeader(r)}
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              {r.commands.map((c) => renderCommandItem(c, r.name))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        {g.commands.map((c) => renderCommandItem(c, g.group))}
                      </div>
                    ))}
                </div>
              );
            })}
          </div>
        )
      ) : groupedFlows.length === 0 ? (
        <EmptyState text="无 flow" />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
          {groupedFlows.map(([cat, flows]) => {
            const catLabel = flows[0]?.category_label || cat;
            const expanded = effectiveExpandedFlowGroups.has(cat);
            return (
              <div key={cat}>
                {/* 分组头 - 沿用命令组头的折叠样式 */}
                <button
                  type="button"
                  data-testid="flow-group-header"
                  className="cliyard-group-header"
                  onClick={() => {
                    setExpandedFlowGroups((prev) => {
                      const next = new Set(prev);
                      if (next.has(cat)) next.delete(cat);
                      else next.add(cat);
                      return next;
                    });
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: space.xs,
                    width: "100%",
                    padding: `0 ${space.xs}px`,
                    marginBottom: space.sm,
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      width: 12,
                      height: 12,
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                      transition: "transform .15s ease",
                      transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
                      color: neutral[500],
                    }}
                  >
                    <svg viewBox="0 0 8 8" width={6} height={6} fill="currentColor">
                      <path d="M1.5 0L6.5 4L1.5 8z" />
                    </svg>
                  </span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: fontSize.sm,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: 0.06,
                      color: neutral[700],
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {catLabel}
                  </span>
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: fontSize.xs,
                      color: neutral[400],
                      fontWeight: 400,
                    }}
                  >
                    {flows.length}
                  </span>
                </button>
                {expanded && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    {flows.map((f) => {
                      const on = selected?.kind === "flow" && selected.target === f.command;
                      const paramCount = flowParamCount(f);
                      return (
                        <button
                          key={f.name}
                          type="button"
                          data-testid="flow-item"
                          data-active={on ? "true" : "false"}
                          onClick={() => onSelect({ kind: "flow", target: f.command })}
                          className="cliyard-flow-item"
                        >
                          {on && <ActiveBar top={14} />}
                          {/* 名称行：mono 名称 + flow pill + 参数数 */}
                          <span style={{ display: "flex", alignItems: "center", gap: space.sm, minWidth: 0 }}>
                            <span
                              className="cliyard-flow-name"
                              style={{
                                fontFamily: fontFamily.mono,
                                fontSize: fontSize.sm,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {f.command}
                            </span>
                            <span
                              style={{
                                flexShrink: 0,
                                borderRadius: radius.pill,
                                padding: "0 6px",
                                backgroundColor: brand[50],
                                border: `1px solid ${brand[200]}`,
                                color: brand[600],
                                fontSize: 9,
                                fontWeight: 600,
                                lineHeight: "14px",
                                whiteSpace: "nowrap",
                              }}
                            >
                              flow
                            </span>
                            {paramCount > 0 && (
                              <span
                                style={{
                                  flexShrink: 0,
                                  borderRadius: radius.pill,
                                  padding: "0 6px",
                                  backgroundColor: neutral[100],
                                  border: `1px solid ${neutral[200]}`,
                                  color: neutral[500],
                                  fontSize: 9,
                                  fontWeight: 600,
                                  lineHeight: "14px",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {paramCount} 参数
                              </span>
                            )}
                          </span>
                          {/* 描述行：两行内省略 */}
                          <span
                            style={{
                              fontSize: fontSize.xs,
                              color: neutral[500],
                              lineHeight: 1.5,
                              overflow: "hidden",
                              display: "-webkit-box",
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: "vertical",
                            }}
                          >
                            {f.description}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
