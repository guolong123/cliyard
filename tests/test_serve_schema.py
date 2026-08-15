"""Tests for YAML spec → command tree / flow tree + JSON Schema converter (plan T2).

Covers:
- ``build_command_tree`` on ``tests/fixtures/spec-dir`` → repos group + types
- labels resolution (list / str / missing) via an explicit tmp_path spec
- ``params_to_json_schema`` type mapping incl. multiple / required / default / x-location
- ``build_flow_schema`` flow params (query/body/header) mapping
- ``build_command_tree`` on ``examples/demo`` → user / pet groups + add_user flow
"""

from pathlib import Path

from cliyard.server.schema_bridge import (
    build_command_tree,
    build_flow_schema,
    params_to_json_schema,
)

SPEC_DIR = Path(__file__).parent / "fixtures" / "spec-dir"
DEMO_DIR = Path(__file__).parent.parent / "examples" / "demo"


# ---------------------------------------------------------------------------
# build_command_tree — fixtures/spec-dir
# ---------------------------------------------------------------------------


def test_command_tree_has_repos_group():
    tree = build_command_tree(SPEC_DIR)
    groups = {g["group"]: g for g in tree["groups"]}

    assert "repos" in groups
    repos = groups["repos"]
    assert repos["desc"] == "Repos"

    commands = {c["name"]: c for c in repos["commands"]}
    assert set(commands) == {"list", "create"}
    assert commands["list"]["method"] == "GET"
    assert commands["list"]["path"] == "repos"
    assert commands["list"]["desc"] == "list"
    assert commands["create"]["method"] == "POST"

    # service metadata propagated from _auth.yaml
    assert tree["service"]["name"] == "test-service"
    assert tree["service"]["description"] == "Test service for cliyard runtime pipeline"


def test_command_tree_two_level_grouping_with_groups_yaml(tmp_path):
    """group 字段 + _groups.yaml：资源聚合到顶层组，desc 取 _groups.yaml。"""
    (tmp_path / "_auth.yaml").write_text(
        "name: t\nserver:\n  base_url: http://x\n", encoding="utf-8"
    )
    (tmp_path / "_groups.yaml").write_text(
        "target:\n  description: 运维资产对象\n", encoding="utf-8"
    )
    (tmp_path / "targets.yaml").write_text(
        "group: target\n"
        "name: manage\n"
        "description: 运维资产对象\n"
        "path: metric/targets\n"
        "methods:\n"
        "  list:\n"
        "    description: 运维资产对象列表\n"
        "    http: {method: GET}\n"
        "  create:\n"
        "    http: {method: POST}\n",
        encoding="utf-8",
    )
    (tmp_path / "targettypes.yaml").write_text(
        "group: target\n"
        "name: type\n"
        "description: 运维资产对象类型\n"
        "path: target/targetTypes\n"
        "methods:\n"
        "  list:\n"
        "    http: {method: GET}\n",
        encoding="utf-8",
    )

    tree = build_command_tree(tmp_path)
    groups = {g["group"]: g for g in tree["groups"]}

    # 仅一个顶层组 target（manage/type 聚合到其下）
    assert set(groups) == {"target"}
    target = groups["target"]
    assert target["desc"] == "运维资产对象"  # 来自 _groups.yaml

    # resources 两级结构：子资源 = 资源 name
    resources = {r["name"]: r for r in target["resources"]}
    assert set(resources) == {"manage", "type"}
    assert resources["manage"]["desc"] == "运维资产对象"
    assert set(c["name"] for c in resources["manage"]["commands"]) == {"list", "create"}
    assert set(c["name"] for c in resources["type"]["commands"]) == {"list"}

    # 兼容字段 commands：两级组以 资源名.方法名 消歧，同名方法不再重复展示
    assert [c["name"] for c in target["commands"]] == [
        "manage.list",
        "manage.create",
        "type.list",
    ]


def test_command_tree_group_desc_fallback_to_resource(tmp_path):
    """group 字段存在但 _groups.yaml 无定义：desc 回退到资源 description。"""
    (tmp_path / "_auth.yaml").write_text(
        "name: t\nserver:\n  base_url: http://x\n", encoding="utf-8"
    )
    (tmp_path / "foo.yaml").write_text(
        "group: custom\nname: foo\ndescription: Foo 资源\n"
        "methods:\n  list:\n    http: {method: GET}\n",
        encoding="utf-8",
    )
    tree = build_command_tree(tmp_path)
    group = next(g for g in tree["groups"] if g["group"] == "custom")
    assert group["desc"] == "Foo 资源"


def test_command_tree_flat_group_empty_resources():
    """无 group 字段的扁平资源：group = 资源 name，resources 为空数组（前端二级扁平）。"""
    tree = build_command_tree(SPEC_DIR)
    repos = next(g for g in tree["groups"] if g["group"] == "repos")

    assert repos["desc"] == "Repos"
    assert repos["resources"] == []
    assert {c["name"] for c in repos["commands"]} == {"list", "create"}


def test_command_tree_no_group_resource_flat(tmp_path):
    """无 group 字段资源（仅 name/path/methods）：输出组 resources==[] 且 commands 非空。"""
    (tmp_path / "_auth.yaml").write_text(
        "name: t\nserver:\n  base_url: http://x\n", encoding="utf-8"
    )
    (tmp_path / "repo.yaml").write_text(
        "name: repo\n"
        "description: 仓库管理\n"
        "path: repos\n"
        "methods:\n"
        "  list:\n"
        "    http: {method: GET}\n"
        "  create:\n"
        "    http: {method: POST}\n",
        encoding="utf-8",
    )

    tree = build_command_tree(tmp_path)
    groups = {g["group"]: g for g in tree["groups"]}

    assert set(groups) == {"repo"}
    repo = groups["repo"]
    assert repo["desc"] == "仓库管理"
    assert repo["resources"] == []
    assert {c["name"] for c in repo["commands"]} == {"list", "create"}
    # commands 与 resources 无冗余嵌套：资源只出现一次（拍平列表）
    assert len(repo["commands"]) == 2


def test_command_tree_labels_empty_when_missing():
    tree = build_command_tree(SPEC_DIR)
    repos = next(g for g in tree["groups"] if g["group"] == "repos")
    commands = {c["name"]: c for c in repos["commands"]}
    assert commands["list"]["labels"] == []
    assert commands["create"]["labels"] == []


def test_command_tree_types_required_default_xlocation():
    tree = build_command_tree(SPEC_DIR)
    repos = next(g for g in tree["groups"] if g["group"] == "repos")
    commands = {c["name"]: c for c in repos["commands"]}

    # list.page: query int with default
    list_schema = commands["list"]["schema"]
    assert list_schema["type"] == "object"
    assert list_schema["title"] == "list"
    assert list_schema["properties"]["page"] == {
        "type": "integer",
        "default": 1,
        "x-location": "query",
    }
    assert list_schema["required"] == []

    # create.name: body string required
    create_schema = commands["create"]["schema"]
    assert create_schema["properties"]["name"] == {
        "type": "string",
        "x-location": "body",
    }
    assert create_schema["required"] == ["name"]


# ---------------------------------------------------------------------------
# params_to_json_schema — pure function mapping
# ---------------------------------------------------------------------------


def test_params_to_json_schema_type_mapping():
    param_list = {
        "query": [
            {"name": "status", "type": "enum", "choices": ["a", "b"],
             "default": "a", "description": "状态"},
            {"name": "limit", "type": "int", "default": 20},
        ],
        "path": [
            {"name": "pet_id", "type": "string", "required": True},
        ],
        "body": [
            {"name": "price", "type": "float"},
            {"name": "avatar", "type": "file"},
            {"name": "meta", "type": "object"},
            {"name": "tags", "type": "string", "multiple": True},
        ],
        "header": [
            {"name": "X-Token", "type": "string", "required": True},
        ],
        "argument": [
            {"name": "force", "type": "bool"},
        ],
    }
    schema = params_to_json_schema(param_list, title="pet-create")

    assert schema["type"] == "object"
    assert schema["title"] == "pet-create"
    props = schema["properties"]

    # enum → string + enum choices; default/description preserved
    assert props["status"] == {
        "type": "string",
        "enum": ["a", "b"],
        "default": "a",
        "description": "状态",
        "x-location": "query",
    }
    # int/integer → integer
    assert props["limit"] == {"type": "integer", "default": 20, "x-location": "query"}
    # string path param
    assert props["pet_id"] == {"type": "string", "x-location": "path"}
    # float → number
    assert props["price"] == {"type": "number", "x-location": "body"}
    # file → string format binary
    assert props["avatar"] == {"type": "string", "format": "binary", "x-location": "body"}
    # json/object → object
    assert props["meta"] == {"type": "object", "x-location": "body"}
    # multiple → array wrapper with single-value items
    assert props["tags"] == {"type": "array", "items": {"type": "string"}, "x-location": "body"}
    # header location preserved
    assert props["X-Token"]["x-location"] == "header"
    # argument location preserved; bool → boolean
    assert props["force"] == {"type": "boolean", "x-location": "argument"}

    # required aggregated at top level (deduplicated)
    assert schema["required"] == ["pet_id", "X-Token"]


def test_params_to_json_schema_integer_alias():
    schema = params_to_json_schema({"query": [{"name": "n", "type": "integer"}]})
    assert schema["properties"]["n"]["type"] == "integer"


def test_params_to_json_schema_multiple_with_enum_items():
    schema = params_to_json_schema(
        {"body": [{"name": "tags", "type": "enum", "choices": ["x", "y"], "multiple": True}]}
    )
    assert schema["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string", "enum": ["x", "y"]},
        "x-location": "body",
    }


def test_params_to_json_schema_required_string_form():
    schema = params_to_json_schema(
        {"query": [{"name": "a", "type": "string", "required": "true"},
                   {"name": "b", "type": "string", "required": "false"}]}
    )
    assert schema["required"] == ["a"]


def test_params_to_json_schema_empty():
    schema = params_to_json_schema(None)
    assert schema == {"type": "object", "properties": {}, "required": []}
    assert params_to_json_schema({}) == {"type": "object", "properties": {}, "required": []}


# ---------------------------------------------------------------------------
# build_flow_schema
# ---------------------------------------------------------------------------


def test_build_flow_schema_empty():
    assert build_flow_schema(None) == {"type": "object", "properties": {}, "required": []}
    assert build_flow_schema({}) == {"type": "object", "properties": {}, "required": []}


def test_build_flow_schema_maps_locations():
    flow_params = {
        "query": [{"name": "name", "type": "string", "required": True,
                   "description": "用户名"}],
        "header": [{"name": "X-Env", "type": "string"}],
    }
    schema = build_flow_schema(flow_params, title="add-user")
    assert schema["title"] == "add-user"
    assert schema["properties"]["name"] == {
        "type": "string",
        "description": "用户名",
        "x-location": "query",
    }
    assert schema["properties"]["X-Env"]["x-location"] == "header"
    assert schema["required"] == ["name"]


# ---------------------------------------------------------------------------
# labels resolution (explicit spec)
# ---------------------------------------------------------------------------


def test_labels_parsing_list_str_and_missing(tmp_path):
    (tmp_path / "_auth.yaml").write_text(
        "name: t\nserver:\n  base_url: http://x\n", encoding="utf-8"
    )
    (tmp_path / "repos.yaml").write_text(
        "description: Repos\n"
        "path: repos\n"
        "methods:\n"
        "  list:\n"
        "    labels: [v2, 已调试]\n"
        "    http: {method: GET}\n"
        "  get:\n"
        "    labels: 已调试\n"
        "    http: {method: GET}\n"
        "  create:\n"
        "    http: {method: POST}\n",
        encoding="utf-8",
    )

    tree = build_command_tree(tmp_path)
    repos = next(g for g in tree["groups"] if g["group"] == "repos")
    commands = {c["name"]: c for c in repos["commands"]}

    assert commands["list"]["labels"] == ["v2", "已调试"]
    assert commands["get"]["labels"] == ["已调试"]
    assert commands["create"]["labels"] == []


# ---------------------------------------------------------------------------
# examples/demo
# ---------------------------------------------------------------------------


def test_demo_has_user_and_pet_groups():
    tree = build_command_tree(DEMO_DIR)
    groups = {g["group"]: g for g in tree["groups"]}
    assert {"user", "pet"} <= set(groups)

    user = next(g for g in tree["groups"] if g["group"] == "user")
    user_cmds = {c["name"] for c in user["commands"]}
    assert {"list", "create", "avatar"} <= user_cmds


def test_demo_group_hierarchy_matches_group_field():
    """demo 中无 group 的 pet/user 渲染为二级扁平（resources=[]），store_order 有 group 为三级。"""
    tree = build_command_tree(DEMO_DIR)
    groups = {g["group"]: g for g in tree["groups"]}

    pet = groups["pet"]
    assert pet["resources"] == []
    assert {c["name"] for c in pet["commands"]} == {"list", "get", "create", "update", "delete"}

    user = groups["user"]
    assert user["resources"] == []

    store = groups["store"]
    order = next(r for r in store["resources"] if r["name"] == "order")
    assert {"list", "place"} <= {c["name"] for c in order["commands"]}


def test_demo_add_user_flow_params_schema():
    tree = build_command_tree(DEMO_DIR)
    flows = {f["name"]: f for f in tree["flows"]}

    assert "add_user" in flows
    flow = flows["add_user"]
    assert flow["command"] == "add-user"
    assert flow["description"] == "新增用户（查→判→创→验）"
    assert flow["step_count"] >= 1

    schema = flow["params_schema"]
    assert "name" in schema["properties"]
    assert "name" in schema["required"]
    assert schema["properties"]["name"]["x-location"] == "query"
    assert schema["properties"]["phone"]["x-location"] == "query"


def test_demo_flow_without_params_returns_empty_schema():
    tree = build_command_tree(DEMO_DIR)
    flows = {f["name"]: f for f in tree["flows"]}
    assert flows["retry_demo"]["params_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }
