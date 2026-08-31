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
  800: "#1E293B",
  700: "#334155",
  600: "#475569",
  500: "#64748B",
  400: "#94A3B8",
  300: "#CBD5E1",
  200: "#E2E8F0",
  100: "#F1F5F9",
  50: "#F8FAFC",
} as const;

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
