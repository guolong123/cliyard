import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  brand,
  neutral,
  space,
  radius,
  fontSize,
  fontFamily,
  statusColors,
  accent,
  tabAccent,
  tabOrder,
  tabLabel,
  type StatusTheme,
  type AccentTheme,
} from "../styles/tokens";
import type { Flow, GroupResource, SpecData, TreeItem, Favorite } from "../api/client";
import { fetchFavorites, toggleFavorite } from "../api/client";

const baseFont: CSSProperties = { fontFamily: fontFamily.body };

export type SideTab = "commands" | "flows" | "favorites";

/** 分组头 accent 轮换顺序（命令/flow 组共享，按索引循环分配）：蓝→紫→绿→琥珀→玫红 */
const GROUP_ACCENTS: AccentTheme[] = [
  accent.blue,
  accent.violet,
  accent.emerald,
  accent.amber,
  accent.rose,
];

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
    background-color: transparent; color: var(--acc-text, #334155);
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
    background-color: transparent; color: var(--acc-text, ${neutral[600]});
    font-size: ${fontSize.sm}px; font-family: ${fontFamily.body};
    transition: background-color .15s ease, color .15s ease;
  }
  .cliyard-flow-item:hover { background-color: ${neutral[100]}; }
  .cliyard-flow-item[data-active="true"] { background-color: ${brand[50]}; }
  .cliyard-flow-item[data-active="true"] .cliyard-flow-name { color: ${brand[600]}; font-weight: 500; }
  .cliyard-flow-item[data-active="true"] .cliyard-flow-command { color: ${brand[500]}; }

  .cliyard-favorite-item {
    position: relative; display: flex; flex-direction: column; gap: 2px;
    width: 100%; padding: ${space.sm - 2}px ${space.md}px ${space.sm - 2}px ${space.md + 4}px;
    border: none; border-radius: 0 ${radius.md}px ${radius.md}px 0;
    cursor: pointer; text-align: left;
    background-color: transparent; color: ${neutral[600]};
    font-size: ${fontSize.sm}px; font-family: ${fontFamily.mono};
    transition: background-color .15s ease, border-color .15s ease;
  }
  .cliyard-favorite-item:hover { background-color: color-mix(in srgb, var(--fav-color, ${neutral[100]}) 15%, ${neutral[50]}); }
  .cliyard-favorite-item[data-active="true"] { background-color: color-mix(in srgb, var(--fav-color, ${brand[50]}) 20%, ${brand[50]}); color: ${brand[600]}; font-weight: 500; }
  .cliyard-favorite-item[data-active="true"]:hover { background-color: color-mix(in srgb, var(--fav-color, ${brand[50]}) 30%, ${brand[50]}); }
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
  // 命令组默认折叠
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  // flow 组默认折叠
  const [expandedFlowGroups, setExpandedFlowGroups] = useState<Set<string>>(
    () => new Set(),
  );

  // 收藏夹
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  useEffect(() => {
    fetchFavorites().then((d) => setFavorites(d.favorites)).catch(() => {});
  }, []);

  // 收藏夹：按分组 + 颜色分配
  const favoriteGroups = useMemo(() => {
    const groupColors = [
      "#3B82F6", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981",
      "#06B6D4", "#F97316", "#6366F1", "#14B8A6", "#84CC16",
      "#E11D48", "#7C3AED", "#0891B2", "#65A30D", "#D946EF",
      "#0EA5E9", "#F43F5E", "#8B5CF6", "#34D399", "#FBBF24",
    ];
    const groups: Record<string, { items: Favorite[]; color: string }> = {};
    let colorIdx = 0;
    for (const f of favorites) {
      const g = f.group || "其他";
      if (!groups[g]) {
        groups[g] = { items: [], color: groupColors[colorIdx % groupColors.length] };
        colorIdx++;
      }
      groups[g].items.push(f);
    }
    return Object.entries(groups)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([group, { items, color }]) => ({ group, items, color }));
  }, [favorites]);

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

  // 预计算 flow 分组 accent（按分组顺序循环分配），避免渲染时 map+indexOf 的 O(n²)
  const flowGroupAccents = useMemo<AccentTheme[]>(
    () => groupedFlows.map((_, i) => GROUP_ACCENTS[i % GROUP_ACCENTS.length]),
    [groupedFlows],
  );

  // 搜索时自动展开所有匹配到的 flow 组；无搜索词时尊重用户折叠状态
  const searchActive = q !== "";

  /** 切换收藏：使用增量 /toggle 端点，避免全量替换竞态 */
  const handleToggleFavorite = async (c: TreeItem, targetPrefix: string, groupName: string) => {
    const target = `${targetPrefix}.${c.name}`;
    const existing = favorites.find((f) => f.target === target);
    // 乐观更新
    let updated: Favorite[];
    if (existing) {
      updated = favorites.filter((f) => f.target !== target);
    } else {
      updated = [
        ...favorites,
        { name: c.name, target, group: groupName, description: c.desc || "" },
      ];
    }
    setFavorites(updated);
    try {
      if (existing) {
        await toggleFavorite(target);
      } else {
        await toggleFavorite(target, {
          name: c.name,
          target,
          group: groupName,
          description: c.desc || "",
        });
      }
    } catch {
      // 回退到服务端状态
      fetchFavorites().then((d) => setFavorites(d.favorites)).catch(() => {});
    }
  };
  const effectiveExpandedFlowGroups = useMemo(() => {
    if (!searchActive) return expandedFlowGroups;
    const all = new Set<string>();
    for (const [cat] of groupedFlows) all.add(cat);
    return all;
  }, [expandedFlowGroups, groupedFlows, searchActive]);

  /** 命令项按钮（target = 资源名.方法名，与 executor 的 resource.method 语义一致） */
  const renderCommandItem = (c: TreeItem, targetPrefix: string, groupName: string, acc: AccentTheme) => {
    const target = `${targetPrefix}.${c.name}`;
    const on = selected?.kind === "command" && selected.target === target;
    const isFav = favorites.some((f) => f.target === target);
    return (
      <div key={target} style={{ display: "flex", alignItems: "stretch", minWidth: 0 }}>
        <button
          type="button"
          data-testid="tree-item"
          data-active={on ? "true" : "false"}
          onClick={() => onSelect({ kind: "command", target })}
          className="cliyard-tree-item"
          style={{ flex: 1, minWidth: 0, ["--acc-text" as string]: acc.text }}
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
                color: neutral[750],
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
        <button
          type="button"
          aria-label={isFav ? "取消收藏" : "收藏"}
          data-testid="star-btn"
          data-active={isFav ? "true" : "false"}
          onClick={(e) => {
            e.stopPropagation();
            handleToggleFavorite(c, targetPrefix, groupName);
          }}
          style={{
            flexShrink: 0,
            width: 32,
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontSize: 14,
            lineHeight: 1,
            color: isFav ? brand[500] : neutral[300],
            padding: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "color .15s ease",
            borderTopRightRadius: radius.md,
            borderBottomRightRadius: radius.md,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = neutral[100]; }}
          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "transparent"; }}
        >
          {isFav ? "★" : "☆"}
        </button>
      </div>
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
        {tabOrder.map((id) => {
          const th = tabAccent[id];
          return (
            <button
              key={id}
              type="button"
              data-testid="side-tab"
              data-active={sideTab === id ? "true" : "false"}
              onClick={() => setSideTab(id)}
              style={{
                border: "none",
                cursor: "pointer",
                padding: `${space.sm}px ${space.md}px`,
                marginBottom: -1,
                fontSize: fontSize.md,
                fontFamily: fontFamily.body,
                borderRadius: `${radius.sm}px ${radius.sm}px 0 0`,
                backgroundColor: sideTab === id ? th.bg : "transparent",
                borderBottom: `2px solid ${sideTab === id ? th.line : "transparent"}`,
                color: sideTab === id ? th.text : neutral[500],
                fontWeight: sideTab === id ? 500 : 400,
                transition: "color .15s ease, border-color .15s ease, background-color .15s ease",
              }}
            >
              {tabLabel[id]}
            </button>
          );
        })}
      </div>

      {sideTab !== "favorites" && (
        /* 搜索（过滤当前 tab 内容） */
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
          placeholder={sideTab === "commands" ? "搜索命令…" : sideTab === "flows" ? "搜索流程…" : "搜索收藏…"}
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
      )}

      {sideTab === "favorites" ? (
        favorites.length === 0 ? (
          <EmptyState text="暂无收藏，在命令上点击 ☆ 添加" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
            {favoriteGroups.map(({ group, items, color }) => (
                <div key={group}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: space.sm,
                      marginBottom: space.sm,
                      padding: `4px 6px`,
                      borderRadius: radius.sm,
                      backgroundColor: `${color}10`,
                    }}
                  >
                    <span
                      style={{
                        fontSize: fontSize.sm,
                        fontWeight: 600,
                        textTransform: "uppercase",
                        letterSpacing: 0.06,
                        color: color,
                      }}
                    >
                      {group}
                    </span>
                    <span style={{ fontSize: fontSize.xs, color: `${color}80` }}>
                      {items.length}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    {items.map((f) => {
                      const on = selected?.kind === "command" && selected.target === f.target;
                      return (
                        <button
                          key={f.target}
                          type="button"
                          className="cliyard-favorite-item"
                          data-active={on ? "true" : "false"}
                          onClick={() => onSelect({ kind: "command", target: f.target })}
                          style={{
                            ["--fav-color" as string]: color,
                          }}
                        >
                          {on && <ActiveBar top={14} />}
                          <span style={{ display: "flex", alignItems: "center", gap: space.sm, minWidth: 0 }}>
                            <span
                              style={{
                                fontFamily: fontFamily.mono,
                                fontSize: fontSize.sm,
                                color: color,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                                ...(on ? { color: brand[600], fontWeight: 500 } : {}),
                              }}
                            >
                              {f.name}
                            </span>
                            <span
                              style={{
                                fontSize: fontSize.xs,
                                color: `${color}80`,
                                fontFamily: fontFamily.mono,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {f.target}
                            </span>
                          </span>
                          {f.description && (
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
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
          </div>
        )
      ) : sideTab === "commands" ? (
        filteredGroups.length === 0 ? (
          <EmptyState text="无命令" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
            {filteredGroups.map((g, i) => {
              const key = `${g.group}-${i}`;
              const expanded = effectiveExpanded.has(key);
              const acc = GROUP_ACCENTS[i % GROUP_ACCENTS.length];
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
                      padding: `4px ${space.xs}px`,
                      marginBottom: space.sm,
                      border: "none",
                      borderRadius: radius.sm,
                      backgroundColor: acc.bg,
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
                        color: acc.text,
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
                        color: acc.text,
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
                              {r.commands.map((c) => renderCommandItem(c, r.name, g.group, acc))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        {g.commands.map((c) => renderCommandItem(c, g.group, g.group, acc))}
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
          {groupedFlows.map(([cat, flows], fgIdx) => {
            const catLabel = flows[0]?.category_label || cat;
            const expanded = effectiveExpandedFlowGroups.has(cat);
            const acc = flowGroupAccents[fgIdx % flowGroupAccents.length];
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
                    padding: `4px ${space.xs}px`,
                    marginBottom: space.sm,
                    border: "none",
                    borderRadius: radius.sm,
                    backgroundColor: acc.bg,
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
                      color: acc.text,
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
                      color: acc.text,
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
                      color: acc.text,
                      fontWeight: 400,
                      opacity: 0.8,
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
                          style={{ ["--acc-text" as string]: acc.text }}
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
