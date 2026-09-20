import os
import pytest

from cliyard.engine.loader import load_cases


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_load_cases_empty_dir_no_file(tmp_path):
    assert load_cases(str(tmp_path)) == []


def test_load_cases_parses_fixture():
    cases = load_cases(os.path.join(FIXTURES, "cases_demo"))
    assert len(cases) == 2
    by_name = {c.name: c for c in cases}
    smoke = by_name["smoke-basic"]
    assert smoke.labels == ["smoke", "daily"]
    assert smoke.flow == "supplier-introduce"
    assert smoke.params["supplier_name"] == "测试供应商A"
    assert len(smoke.assert_) == 1
    assert smoke.assert_[0].jsonpath == "$.code"
    assert smoke.assert_[0].expected == "0"
    csp = by_name["csp-check"]
    assert len(csp.data) == 2
    assert csp.data[1]["company_id"] == 9999
    assert len(csp.assert_) == 1
    assert csp.assert_[0].operator == "eq"
    assert csp.assert_[0].expected == "200"


def test_load_cases_missing_flow_raises(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "_cases.yaml").write_text(
        "cases:\n  bad:\n    description: no flow\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_cases(str(tmp_path))