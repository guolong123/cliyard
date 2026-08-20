import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import CommandTree from "../CommandTree";
import type { Selection } from "../CommandTree";
import type { SpecData } from "../../api/client";

/** mock spec：1 个命令分组（含 labels）+ 1 个 flow（含 1 个参数）。
 * resources=[] 模拟后端无 group 资源的扁平组输出（二级：命令直接挂组下）。 */
const spec: SpecData = {
  service: { name: "demo", description: "演示服务" },
  groups: [
    {
      group: "repos",
      desc: "仓库管理",
      resources: [],
      commands: [
        {
          name: "list",
          labels: ["已调试", "v2"],
          desc: "列出仓库",
          path: "repos",
          method: "GET",
          schema: { type: "object", properties: {} },
        },
        {
          name: "delete",
          labels: [],
          desc: "删除仓库",
          path: "repos",
          method: "DELETE",
          schema: { type: "object", properties: {} },
        },
      ],
    },
  ],
  flows: [
    {
      name: "add_user",
      description: "新增用户（查→判→创→验）",
      command: "add-user",
      category: "eco-inquiry",
      category_label: "生态询价",
      labels: [],
      params_schema: { type: "object", properties: { name: {} } },
      step_count: 4,
    },
  ],
};

const emptySpec: SpecData = { service: { name: "demo", description: "" }, groups: [], flows: [] };

/** mock spec：两级分组（group → resources，模拟 ketacli target 组） */
const twoLevelSpec: SpecData = {
  service: { name: "demo", description: "演示服务" },
  groups: [
    {
      group: "target",
      desc: "运维资产对象",
      commands: [
        {
          name: "list",
          labels: [],
          desc: "运维资产对象列表",
          path: "metric/targets",
          method: "GET",
          schema: { type: "object", properties: {} },
        },
        {
          name: "list",
          labels: [],
          desc: "运维资产对象类型列表",
          path: "target/targetTypes",
          method: "GET",
          schema: { type: "object", properties: {} },
        },
      ],
      resources: [
        {
          name: "manage",
          desc: "运维资产对象",
          commands: [
            {
              name: "list",
              labels: ["已调试"],
              desc: "运维资产对象列表",
              path: "metric/targets",
              method: "GET",
              schema: { type: "object", properties: {} },
            },
          ],
        },
        {
          name: "type",
          desc: "运维资产对象类型",
          commands: [
            {
              name: "list",
              labels: [],
              desc: "运维资产对象类型列表",
              path: "target/targetTypes",
              method: "GET",
              schema: { type: "object", properties: {} },
            },
          ],
        },
      ],
    },
  ],
  flows: [],
};

/** mock spec：2 个同名分组（templates）——模拟 /api/spec 重复 group 名（合法输入） */
const dupGroupSpec: SpecData = {
  service: { name: "demo", description: "演示服务" },
  groups: [
    {
      group: "templates",
      desc: "模板分组 A",
      commands: [
        {
          name: "list",
          labels: [],
          desc: "列出模板 A",
          path: "templates",
          method: "GET",
          schema: { type: "object", properties: {} },
        },
      ],
    },
    {
      group: "templates",
      desc: "模板分组 B",
      commands: [
        {
          name: "create",
          labels: [],
          desc: "创建模板 B",
          path: "templates",
          method: "POST",
          schema: { type: "object", properties: {} },
        },
      ],
    },
  ],
  flows: [
    {
      name: "add_user",
      description: "新增用户",
      command: "add-user",
      category: "",
      category_label: "",
      labels: [],
      params_schema: { type: "object", properties: {} },
      step_count: 2,
    },
  ],
};

function renderTree(selected: Selection | null = null, onSelect = vi.fn()) {
  return render(<CommandTree spec={spec} selected={selected} onSelect={onSelect} />);
}

describe("CommandTree", () => {
  it("渲染命令分组标题、命令项与 labels pill", () => {
    renderTree();
    // 分组标题（uppercase repos）
    expect(screen.getByText("repos")).toBeInTheDocument();
    expect(screen.getByText("仓库管理")).toBeInTheDocument();
    // 命令项
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    expect(screen.getByText("list")).toBeInTheDocument();
    expect(screen.getByText("delete")).toBeInTheDocument();
    // labels pill
    expect(screen.getByText("已调试")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("tab 切换过滤：命令 ↔ Flow 内容互斥，placeholder 随 tab 变化", () => {
    renderTree();
    const tabs = screen.getAllByTestId("side-tab");
    expect(tabs).toHaveLength(2);

    // 初始：命令 tab 生效
    expect(screen.getByPlaceholderText("搜索命令…")).toBeInTheDocument();
    expect(screen.getByText("list")).toBeInTheDocument();

    // 切到 Flow：命令项消失，flow 项出现（主名显示 command 形式）
    fireEvent.click(tabs[1]);
    expect(screen.getByPlaceholderText("搜索 flow…")).toBeInTheDocument();
    expect(screen.queryByText("list")).not.toBeInTheDocument();
    expect(screen.getByText("add-user")).toBeInTheDocument();
    // flow 参数数 pill
    expect(screen.getByText("1 参数")).toBeInTheDocument();

    // 切回命令：恢复
    fireEvent.click(tabs[0]);
    expect(screen.getByText("list")).toBeInTheDocument();
    expect(screen.queryByText("add-user")).not.toBeInTheDocument();
  });

  it("搜索框按当前 tab 过滤内容", () => {
    renderTree();
    const input = screen.getByPlaceholderText("搜索命令…");

    // 命令 tab：按名称过滤
    fireEvent.change(input, { target: { value: "list" } });
    expect(screen.getByText("list")).toBeInTheDocument();
    expect(screen.queryByText("delete")).not.toBeInTheDocument();

    // 清空搜索，切 Flow tab：按 command 过滤
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getAllByTestId("side-tab")[1]);
    const flowInput = screen.getByPlaceholderText("搜索 flow…");
    fireEvent.change(flowInput, { target: { value: "add-user" } });
    expect(screen.getByText("add-user")).toBeInTheDocument();

    // 无匹配 → 空态
    fireEvent.change(flowInput, { target: { value: "zzz" } });
    expect(screen.getByText("无 flow")).toBeInTheDocument();
  });

  it("点击命令/flow 触发 onSelect（命令 = resource.method，flow = command）", () => {
    const onSelect = vi.fn();
    renderTree(null, onSelect);

    // 命令项
    fireEvent.click(screen.getByText("list"));
    expect(onSelect).toHaveBeenCalledWith<[Selection]>({ kind: "command", target: "repos.list" });

    // flow 项（target 用 flow.command）
    fireEvent.click(screen.getAllByTestId("side-tab")[1]);
    fireEvent.click(screen.getByText("add-user"));
    expect(onSelect).toHaveBeenCalledWith<[Selection]>({ kind: "flow", target: "add-user" });
  });

  it("选中项显示激活态（选中判定：命令 kind+target 匹配）", () => {
    renderTree({ kind: "command", target: "repos.list" });
    const [list, deleteBtn] = screen.getAllByTestId("tree-item");
    expect(list.getAttribute("data-active")).toBe("true");
    expect(deleteBtn.getAttribute("data-active")).toBe("false");
  });

  it("重复分组名（合法输入）：命令/Flow 反复切换无命令项泄漏", () => {
    render(<CommandTree spec={dupGroupSpec} selected={null} onSelect={vi.fn()} />);
    const tabs = screen.getAllByTestId("side-tab");

    // 命令 tab：同名分组各渲染一次，命令项全部可见
    expect(screen.getByText("模板分组 A")).toBeInTheDocument();
    expect(screen.getByText("模板分组 B")).toBeInTheDocument();
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);

    for (let i = 0; i < 5; i++) {
      // 切到 Flow：flow 容器内无命令项（tree-item）泄漏
      fireEvent.click(tabs[1]);
      expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);
      expect(screen.getAllByTestId("flow-item")).toHaveLength(1);

      // 切回命令：命令项数量稳定，不随切换累积
      fireEvent.click(tabs[0]);
      expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    }
  });

  it("空 spec 显示空态「无命令」/「无 flow」", () => {
    render(<CommandTree spec={emptySpec} selected={null} onSelect={vi.fn()} />);
    expect(screen.getByText("无命令")).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId("side-tab")[1]);
    expect(screen.getByText("无 flow")).toBeInTheDocument();
  });
});

describe("CommandTree 扁平分组", () => {
  it("resources=[]：组标题下直接渲染命令项，无资源小节标题", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    // 命令项直接挂组下
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    // 组标题唯一：扁平分支不额外渲染资源小节标题（三级分支会出现第二个 "repos"）
    expect(screen.getAllByText("repos")).toHaveLength(1);
  });

  it("resources=[]：选中回调 target = 组名.方法名", () => {
    const onSelect = vi.fn();
    render(<CommandTree spec={spec} selected={null} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByTestId("tree-item")[0]);
    expect(onSelect).toHaveBeenCalledWith<[Selection]>({ kind: "command", target: "repos.list" });
  });

  it("resources=[]：搜索按 组名.命令名/desc 过滤", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("搜索命令…"), { target: { value: "repos.list" } });
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);
    expect(screen.queryByText("delete")).not.toBeInTheDocument();
  });
});

describe("CommandTree 两级分组", () => {
  it("两级分组：组标题/组 desc + 子资源名/desc + 资源下命令项", () => {
    render(<CommandTree spec={twoLevelSpec} selected={null} onSelect={vi.fn()} />);
    expect(screen.getByText("target")).toBeInTheDocument();
    expect(screen.getAllByText("运维资产对象").length).toBeGreaterThanOrEqual(2); // 组 desc + manage 资源 desc
    expect(screen.getByText("manage")).toBeInTheDocument();
    expect(screen.getByText("type")).toBeInTheDocument();
    expect(screen.getByText("运维资产对象类型")).toBeInTheDocument();
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    expect(screen.getByText("已调试")).toBeInTheDocument(); // manage.list 的 labels pill
  });

  it("两级分组：选中回调 target = 资源名.方法名（manage.list，非 target.list）", () => {
    const onSelect = vi.fn();
    render(<CommandTree spec={twoLevelSpec} selected={null} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByTestId("tree-item")[0]);
    expect(onSelect).toHaveBeenCalledWith<[Selection]>({ kind: "command", target: "manage.list" });
  });

  it("两级分组：选中态按资源名.方法名匹配", () => {
    render(
      <CommandTree spec={twoLevelSpec} selected={{ kind: "command", target: "type.list" }} onSelect={vi.fn()} />,
    );
    const [manage, type] = screen.getAllByTestId("tree-item");
    expect(manage.getAttribute("data-active")).toBe("false");
    expect(type.getAttribute("data-active")).toBe("true");
  });

  it("两级分组：搜索匹配子资源名/命令名，只保留命中资源", () => {
    render(<CommandTree spec={twoLevelSpec} selected={null} onSelect={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("搜索命令…"), {
      target: { value: "type" },
    });
    expect(screen.getByText("运维资产对象类型")).toBeInTheDocument();
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);
    expect(screen.queryByText("已调试")).not.toBeInTheDocument(); // manage 资源被过滤
  });
});

describe("CommandTree 分组折叠/展开", () => {
  it("默认全部展开：命令项可见", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    expect(screen.getByText("list")).toBeInTheDocument();
    expect(screen.getByText("delete")).toBeInTheDocument();
  });

  it("点击分组标题折叠：命令项隐藏", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");
    expect(headers).toHaveLength(1);

    fireEvent.click(headers[0]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);
  });

  it("再次点击分组标题展开：命令项恢复", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");

    fireEvent.click(headers[0]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);

    fireEvent.click(headers[0]);
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
  });

  it("搜索时自动展开匹配组（即使被折叠）", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");

    // 先折叠
    fireEvent.click(headers[0]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);

    // 搜索匹配
    fireEvent.change(screen.getByPlaceholderText("搜索命令…"), { target: { value: "list" } });
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);
    expect(screen.getByText("list")).toBeInTheDocument();
  });

  it("清空搜索后恢复折叠状态", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");

    // 折叠
    fireEvent.click(headers[0]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);

    // 搜索（自动展开）
    fireEvent.change(screen.getByPlaceholderText("搜索命令…"), { target: { value: "list" } });
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);

    // 清空搜索（恢复折叠状态）
    fireEvent.change(screen.getByPlaceholderText("搜索命令…"), { target: { value: "" } });
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);
  });

  it("两级分组：折叠/展开影响子资源和命令项", () => {
    render(<CommandTree spec={twoLevelSpec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");

    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    expect(screen.getByText("manage")).toBeInTheDocument();
    expect(screen.getByText("type")).toBeInTheDocument();

    // 折叠
    fireEvent.click(headers[0]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);
    expect(screen.queryByText("manage")).not.toBeInTheDocument();

    // 展开
    fireEvent.click(headers[0]);
    expect(screen.getAllByTestId("tree-item")).toHaveLength(2);
    expect(screen.getByText("manage")).toBeInTheDocument();
  });

  it("重复分组名：折叠/展开独立控制", () => {
    render(<CommandTree spec={dupGroupSpec} selected={null} onSelect={vi.fn()} />);
    const headers = screen.getAllByTestId("group-header");
    expect(headers).toHaveLength(2);

    // 折叠第一个
    fireEvent.click(headers[0]);
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);

    // 折叠第二个
    fireEvent.click(headers[1]);
    expect(screen.queryAllByTestId("tree-item")).toHaveLength(0);

    // 展开第一个
    fireEvent.click(headers[0]);
    expect(screen.getAllByTestId("tree-item")).toHaveLength(1);
  });

  it("Flow Tab：默认显示分组头部（flow-group-header）", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    // 切到 Flow Tab
    const flowTab = screen.getByText("Flow");
    fireEvent.click(flowTab);
    expect(screen.getByTestId("flow-group-header")).toBeInTheDocument();
  });

  it("Flow Tab：默认折叠，flow-item 不可见", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByText("Flow"));
    expect(screen.queryAllByTestId("flow-item")).toHaveLength(0);
  });

  it("Flow Tab：点击分组头展开，flow-item 可见", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByText("Flow"));
    const header = screen.getByTestId("flow-group-header");
    fireEvent.click(header);
    expect(screen.getAllByTestId("flow-item")).toHaveLength(1);
    expect(screen.getByText("add-user")).toBeInTheDocument();
  });

  it("Flow Tab：搜索 category_label 命中展开", () => {
    render(<CommandTree spec={spec} selected={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByText("Flow"));
    // 搜索 category_label 中文名
    fireEvent.change(screen.getByPlaceholderText("搜索 flow…"), { target: { value: "生态询价" } });
    expect(screen.getAllByTestId("flow-item")).toHaveLength(1);
    expect(screen.getByText("add-user")).toBeInTheDocument();
  });
});
