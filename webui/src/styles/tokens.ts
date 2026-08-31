/**
 * cliyard-web 设计 token
 * =============================================
 * 值提取自 docs/cliyard-web/prototypes/_shared/styles.ts（原型唯一 token 源），
 * 保持完全相同：所有颜色/间距/圆角/字号/阴影收敛在此，组件内不散落 magic number。
 * 另有 global.css 以 CSS 变量承载同一组值，供纯 CSS 场景（reset / 通用 class）使用。
 */

/* ---------------------------------- 品牌色 ---------------------------------- */
export const brand = {
  500: "#3B82F6",
  600: "#2563EB",
  700: "#1D4ED8",
  50: "#EFF6FF",
  100: "#DBEAFE",
  200: "#BFDBFE",
} as const;

/* ---------------------------------- 语义色 ---------------------------------- */
export type StatusKey = "success" | "error" | "running" | "warning";

export interface StatusTheme {
  color: string;
  bg: string;
  border: string;
}

/** 状态语义色：成功=绿 / 失败=红 / 运行中=蓝 / 警告=琥珀（浅底 + 1px 彩边 + 深色文字） */
export const statusColors: Record<StatusKey, StatusTheme> = {
  success: { color: "#059669", bg: "#ECFDF5", border: "#A7F3D0" },
  error: { color: "#DC2626", bg: "#FEF2F2", border: "#FECACA" },
  running: { color: "#2563EB", bg: "#EFF6FF", border: "#BFDBFE" },
  warning: { color: "#D97706", bg: "#FFFBEB", border: "#FDE68A" },
};

/* ---------------------------------- 中性色 ---------------------------------- */
export const neutral = {
  900: "#0F172A",
  850: "#23272E",
  800: "#1E293B",
  750: "#2A2F37",
  700: "#334155",
  600: "#475569",
  500: "#64748B",
  400: "#94A3B8",
  300: "#CBD5E1",
  200: "#E2E8F0",
  100: "#F1F5F9",
  50: "#F8FAFC",
} as const;

/* ---------------------------------- accent 调色板（tab/分组/命令/流程着色） ---------------------------------- */
export interface AccentTheme {
  /** 主色文字（对比度最高） */
  text: string;
  /** 浅底 */
  bg: string;
  /** 选中/主色强调 */
  line: string;
}

/** tab 与分组共享的 accent 语义色：蓝（命令/默认）、紫（常用）、绿（流程）、琥珀、玫红 */
export const accent: { blue: AccentTheme; violet: AccentTheme; emerald: AccentTheme; amber: AccentTheme; rose: AccentTheme } = {
  blue: { text: brand[700], bg: brand[50], line: brand[500] },
  violet: { text: "#6D28D9", bg: "#F5F3FF", line: "#8B5CF6" },
  emerald: { text: "#047857", bg: "#ECFDF5", line: "#10B981" },
  amber: { text: "#B45309", bg: "#FFFBEB", line: "#F59E0B" },
  rose: { text: "#BE123C", bg: "#FFF1F2", line: "#F43F5E" },
} as const;

/** tab 主题映射：命令=蓝、常用=紫、流程=绿 */
export const tabAccent: Record<"commands" | "flows" | "favorites", AccentTheme> = {
  commands: accent.blue,
  flows: accent.emerald,
  favorites: accent.violet,
};

/** tab 顺序数组（渲染用），保持与 tabAccent 一一对应 */
export const tabOrder = ["commands", "favorites", "flows"] as const;

/** tab 显示文本 */
export const tabLabel: Record<"commands" | "flows" | "favorites", string> = {
  commands: "命令",
  flows: "流程",
  favorites: "⭐ 常用命令",
};

/* ---------------------------------- 间距（4px 基准） ---------------------------------- */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/* ---------------------------------- 圆角 ---------------------------------- */
export const radius = { sm: 6, md: 10, lg: 14, pill: 999 } as const;

/* ---------------------------------- 字号 ---------------------------------- */
export const fontSize = {
  xs: 12,
  sm: 13,
  md: 14,
  lg: 16,
  xl: 19,
  xxl: 23,
} as const;

/* ---------------------------------- 字体 ---------------------------------- */
export const fontFamily = {
  body: `"PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", -apple-system, "Segoe UI", sans-serif`,
  mono: `"JetBrains Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace`,
} as const;

/* ---------------------------------- 阴影 ---------------------------------- */
export const shadow = {
  sm: "0 1px 2px rgba(15,23,42,.05), 0 1px 3px rgba(15,23,42,.08)",
  md: "0 4px 14px rgba(15,23,42,.08), 0 2px 4px rgba(15,23,42,.05)",
  lg: "0 16px 40px rgba(15,23,42,.14)",
  /** 品牌蓝按钮光晕 */
  brand: "0 6px 16px rgba(37,99,235,.3)",
} as const;
