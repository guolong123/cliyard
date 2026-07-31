from cliyard.engine.builder import ServiceContext, execute_pipeline


class _FakeResponse:
    def __init__(self, text="", json_payload=None):
        self.text = text
        self.status_code = 200
        self.headers = {"Content-Type": "text/xml"}
        self._json = json_payload

    def json(self):
        if self._json is not None:
            return self._json
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class _FakeHttpClient:
    def __init__(self, response):
        self._response = response
        self.default_headers = {}

    def request(self, **kwargs):
        return self._response


def _run_pipeline(response):
    client = _FakeHttpClient(response)
    return execute_pipeline(
        {},
        {"http": {"method": "GET", "path": "/job/x/config.xml"}},
        {"path": "jobs"},
        ServiceContext(base_url="http://test.local"),
        http_client=client,
    )


def test_xml_response_returns_raw_text():
    assert _run_pipeline(_FakeResponse(text="<project/>")) == "<project/>"


def test_empty_response_returns_empty_dict():
    assert _run_pipeline(_FakeResponse(text="")) == {}


def test_whitespace_only_response_returns_empty_dict():
    assert _run_pipeline(_FakeResponse(text="   \n  ")) == {}


def test_json_response_still_parsed():
    assert _run_pipeline(_FakeResponse(json_payload={"ok": True})) == {"ok": True}


def test_raw_text_with_items_path_does_not_attempt_parse():
    client = _FakeHttpClient(_FakeResponse(text="<project/>"))
    result = execute_pipeline(
        {},
        {"http": {"method": "GET", "path": "/job/x/config.xml"}, "output": {"items_path": "$.items"}},
        {"path": "jobs"},
        ServiceContext(base_url="http://test.local"),
        http_client=client,
    )
    assert result == "<project/>"
