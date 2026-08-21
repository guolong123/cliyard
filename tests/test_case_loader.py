"""Tests for cliyard.engine.loader.load_cases and CaseSpec."""

from __future__ import annotations

import pytest

from cliyard.engine.case import CaseSpec
from cliyard.engine.loader import load_cases


def _write_cases(tmp_path, content: str, *, subdir: bool = False) -> None:
    base = tmp_path / "cases" if subdir else tmp_path
    base.mkdir(exist_ok=True)
    (base / "_cases.yaml").write_text(content, encoding="utf-8")


class TestLoadCases:
    def test_full_field_parse_when_all_fields_present(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  create-repo:
    name: 创建仓库
    description: 创建一个新仓库并校验结果
    kind: flow
    target: flow.run
    labels: [smoke, repo]
    params:
      name: demo
      private: true
    asserts:
      - eq: ["$.code", 0]
""")
        cases = load_cases(tmp_path)

        assert len(cases) == 1
        case = cases[0]
        assert isinstance(case, CaseSpec)
        assert case.name == "创建仓库"
        assert case.description == "创建一个新仓库并校验结果"
        assert case.kind == "flow"
        assert case.target == "flow.run"
        assert case.labels == ["smoke", "repo"]
        assert case.params == {"name": "demo", "private": True}
        assert case.asserts == [{"eq": ["$.code", 0]}]

    def test_defaults_when_minimal_entry(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  minimal: {}
""")
        cases = load_cases(tmp_path)

        assert len(cases) == 1
        case = cases[0]
        assert case.name == "minimal"
        assert case.description == ""
        assert case.kind == "command"
        assert case.target == ""
        assert case.labels == []
        assert case.params == {}
        assert case.asserts == []

    def test_missing_name_warns_and_skips_but_others_load(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  ~:
    target: some.cmd
  good-case:
    target: other.cmd
""")
        with pytest.warns(UserWarning, match="no name"):
            cases = load_cases(tmp_path)

        assert [c.name for c in cases] == ["good-case"]

    def test_no_file_returns_empty(self, tmp_path):
        assert load_cases(tmp_path) == []

    def test_scalar_labels_wrapped(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  scalar-labels:
    target: x.y
    labels: smoke
""")
        cases = load_cases(tmp_path)

        assert cases[0].labels == ["smoke"]

    def test_invalid_kind_warns_and_coerced_to_command(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  weird:
    kind: suite
    target: x.y
""")
        with pytest.warns(UserWarning, match="kind"):
            cases = load_cases(tmp_path)

        assert cases[0].kind == "command"

    def test_cases_dir_preferred_over_root(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  from-root:
    target: root.cmd
""")
        _write_cases(tmp_path, """
cases:
  from-subdir:
    target: subdir.cmd
""", subdir=True)

        cases = load_cases(tmp_path)

        assert [c.name for c in cases] == ["from-subdir"]


class TestLoadCasesAdversarial:
    def test_yaml_list_document_raises_value_error(self, tmp_path):
        _write_cases(tmp_path, "- a\n- b\n")

        with pytest.raises(ValueError, match="mapping"):
            load_cases(tmp_path)

    def test_scalar_entry_warns_and_skips(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  broken: just-a-string
  ok:
    target: x.y
""")
        with pytest.warns(UserWarning, match="must be a mapping"):
            cases = load_cases(tmp_path)

        assert [c.name for c in cases] == ["ok"]

    def test_non_dict_params_warns_and_defaults_empty(self, tmp_path):
        _write_cases(tmp_path, """
cases:
  badparams:
    target: x.y
    params: not-a-dict
""")
        with pytest.warns(UserWarning, match="params"):
            cases = load_cases(tmp_path)

        assert cases[0].params == {}

    def test_empty_cases_section_returns_empty(self, tmp_path):
        _write_cases(tmp_path, "cases:\n")

        assert load_cases(tmp_path) == []
