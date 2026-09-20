import { forwardRef, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { FieldProps, RJSFSchema, UiSchema } from "@rjsf/utils";
import { execute } from "../api/client";
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

export interface CommandFormProps {
  kind: "command" | "flow" | "case";
  target: string;
  schema: Record<string, unknown> | null;
  onExecute: (executionId: string, params?: Record<string, unknown>) => void;
}

/** rjsf scoped 样式：label mono 小字、输入框 focus 品牌蓝边框（对齐原型 FormField / .cliyard-field） */
const formCss = `
  .cliyard-form .form-group {
    margin-bottom: ${space.lg}px;
    display: flex; flex-direction: column; gap: ${space.sm}px;
  }
  .cliyard-form .control-label {
    font-family: ${fontFamily.mono}; font-size: ${fontSize.xs}px;
    font-weight: 600; color: ${neutral[700]};
  }
  .cliyard-form .field-description { font-size: ${fontSize.xs}px; color: ${neutral[400]}; }
  .cliyard-form .required { color: ${statusColors.error.color}; margin-left: 2px; }
  .cliyard-form fieldset { border: none; margin: 0; padding: 0; }
  .cliyard-form legend { display: none; }
  .cliyard-form input[type="text"], .cliyard-form input[type="password"],
  .cliyard-form input[type="number"], .cliyard-form select, .cliyard-form textarea {
    width: 100%; border-radius: ${radius.md}px;
    border: 1px solid ${neutral[200]}; background-color: #FFFFFF;
    padding: ${space.sm}px ${space.md}px;
    font-size: ${fontSize.md}px; color: ${neutral[800]};
    font-family: ${fontFamily.mono};
    outline: none; box-shadow: ${shadow.sm}; box-sizing: border-box;
    transition: border-color .15s ease, box-shadow .15s ease;
  }
  .cliyard-form input:focus, .cliyard-form select:focus, .cliyard-form textarea:focus {
    border-color: ${brand[500]}; box-shadow: 0 0 0 3px rgba(59,130,246,.15);
  }
  .cliyard-form .field-boolean.checkbox { flex-direction: row; align-items: center; }
  .cliyard-form .field-boolean.checkbox label {
    display: flex; align-items: center; gap: ${space.sm}px;
    font-family: ${fontFamily.mono}; font-size: ${fontSize.md}px;
    font-weight: 500; color: ${neutral[700]}; margin: 0; cursor: pointer;
  }
  .cliyard-form input[type="file"] { padding: ${space.sm}px; }
`;

/**
 * TagsField：array 类型字段渲染为全宽文本输入框。
 * 用户输入逗号分隔的多个值，提交时拆分为数组。
 * 替代 rjsf 默认的「小方块 + 添加按钮」控件，与单值输入框风格统一。
 */
function TagsField({ schema, value, onChange, disabled, id, required }: FieldProps) {
  const inputId = id || "tags-input";
  const title = (schema?.title as string) || "";
  const description = (schema?.description as string) || "";
  const arr: string[] = Array.isArray(value) ? (value as string[]) : [];

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    const items = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    onChange(items);
  };

  const tagsInputCss: CSSProperties = {
    width: "100%",
    borderRadius: radius.md,
    border: `1px solid ${neutral[200]}`,
    backgroundColor: "#FFFFFF",
    padding: `${space.sm}px ${space.md}px`,
    fontSize: fontSize.md,
    color: neutral[800],
    fontFamily: fontFamily.mono,
    outline: "none",
    boxShadow: shadow.sm,
    boxSizing: "border-box",
    transition: "border-color .15s ease, box-shadow .15s ease",
  };

  return (
    <div className="form-group" style={{ marginBottom: space.lg, display: "flex", flexDirection: "column", gap: space.sm }}>
      <label
        htmlFor={inputId}
        style={{
          fontFamily: fontFamily.mono,
          fontSize: fontSize.xs,
          fontWeight: 600,
          color: neutral[700],
        }}
      >
        {title}
        {required && <span style={{ color: statusColors.error.color, marginLeft: 2 }}>*</span>}
      </label>
      {description && (
        <div style={{ fontSize: fontSize.xs, color: neutral[400] }}>{description}</div>
      )}
      <input
        id={inputId}
        type="text"
        style={tagsInputCss}
        disabled={disabled}
        placeholder="多个值用逗号分隔"
        value={arr.join(", ")}
        onChange={handleChange}
      />
    </div>
  );
}

/** 字段 widget 映射：file → 文件上传（产出 base64），password → 密码框；array → TagsField */
function buildUiSchema(schema: Record<string, unknown>): UiSchema {
  const properties = (schema.properties ?? {}) as Record<string, RJSFSchema>;
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  for (const name of Object.keys(properties)) {
    const prop = properties[name];
    if (prop.type === "array") ui[name] = { "ui:field": "tags" };
    else if (prop.format === "binary") ui[name] = { "ui:widget": "file" };
    else if (prop.format === "password") ui[name] = { "ui:widget": "password" };
  }
  return ui;
}

/** file widget 产出 {name, size, type, content}，后端桥接期望 content（base64） */
function flattenFileParams(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v && typeof v === "object" && "content" in v && typeof v.content === "string") {
      out[k] = v.content;
    } else {
      out[k] = v;
    }
  }
  return out;
}

/** 暴露给父级（StepsPanel 底部按钮）的句柄 */
export interface CommandFormHandle {
  submit: () => void;
}

/**
 * 中间表单：rjsf 按选中命令/flow 的 JSON Schema 自动渲染。
 * 提交调 POST /api/execute，拿到 execution_id 后回调父级（App 传给 StepsPanel 订阅 SSE）。
 * forwardRef 暴露 submit() 供 StepsPanel 底部固定按钮调用。
 */
const CommandForm = forwardRef<CommandFormHandle, CommandFormProps>(function CommandForm(
  { kind, target, schema, onExecute },
  ref,
) {
  const formRef = useRef<Form>(null);
  const [formKey, setFormKey] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const empty =
    !schema ||
    typeof schema.properties !== "object" ||
    Object.keys(schema.properties as object).length === 0;
  const uiSchema = useMemo(() => (schema ? buildUiSchema(schema) : {}), [schema]);

  const run = async (params: Record<string, unknown>) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const { execution_id } = await execute(kind, target, params);
      onExecute(execution_id, params);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  useImperativeHandle(ref, () => ({
    submit() {
      if (empty) {
        void run({});
      } else {
        formRef.current?.submit();
      }
    },
  }));

  return (
    <section
      data-testid="command-form"
      style={{
        width: 320,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#FFFFFF",
        border: `1px solid ${neutral[200]}`,
        borderRadius: radius.lg,
        boxShadow: shadow.sm,
        padding: space.lg,
        overflow: "hidden",
      }}
    >
      <style>{formCss}</style>

      {/* 标题：mono 大标题 + 副标题 */}
      <div style={{ marginBottom: space.lg }}>
        <h2
          style={{
            margin: 0,
            fontFamily: fontFamily.mono,
            fontSize: fontSize.lg,
            fontWeight: 600,
            color: neutral[900],
          }}
        >
          {target}
        </h2>
        <p style={{ margin: `${space.xs}px 0 0`, fontSize: fontSize.xs, color: neutral[400], ...baseFont }}>
          {kind === "command" ? "由 YAML spec 自动渲染" : kind === "flow" ? "由 _flows.yaml 注册" : "由 _cases.yaml 注册"}
        </p>
      </div>

      {empty ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            borderRadius: radius.md,
            border: `1px dashed ${neutral[200]}`,
            backgroundColor: neutral[50],
            color: neutral[400],
            fontSize: fontSize.sm,
            ...baseFont,
          }}
        >
          该流程无需参数
        </div>
      ) : (
        <div className="cliyard-form" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <Form
            key={formKey}
            ref={formRef}
            schema={schema as RJSFSchema}
            uiSchema={uiSchema}
            validator={validator}
            fields={{ tags: TagsField }}
            onSubmit={({ formData }) => {
              void run(flattenFileParams((formData ?? {}) as Record<string, unknown>));
            }}
          />
        </div>
      )}

      {submitError && (
        <div
          data-testid="submit-error"
          style={{
            flexShrink: 0,
            marginTop: space.sm,
            fontSize: fontSize.xs,
            color: statusColors.error.color,
            ...baseFont,
          }}
        >
          执行失败：{submitError}
        </div>
      )}

      <div
        style={{
          flexShrink: 0,
          display: "flex",
          gap: space.sm,
          borderTop: `1px solid ${neutral[100]}`,
          paddingTop: space.lg,
          marginTop: "auto",
        }}
      >
        <button
          type="button"
          data-testid="run-button"
          className="cliyard-pill-btn"
          disabled={submitting}
          onClick={() => {
            if (empty) {
              void run({});
            } else {
              formRef.current?.submit();
            }
          }}
          style={{ flex: 1, padding: `${space.sm + 2}px ${space.lg}px` }}
        >
          {submitting ? "执行中…" : kind === "command" ? "执行" : kind === "flow" ? "运行流程" : "运行 Case"}
        </button>
        <button
          type="button"
          data-testid="reset-button"
          className="cliyard-outline-btn"
          onClick={() => {
            setFormKey((k) => k + 1);
            setSubmitError(null);
          }}
          style={{ flex: 1, justifyContent: "center" }}
        >
          重置
        </button>
      </div>
    </section>
  );
});

export default CommandForm;
