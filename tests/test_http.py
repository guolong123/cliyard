from unittest.mock import MagicMock, patch

import pytest

from cliyard.client.http import HttpClient, request


class TestHttpClientRequest:
    """Tests for ``HttpClient.request()`` — especially DELETE JSON body handling."""

    @pytest.fixture
    def client(self):
        return HttpClient(base_url="http://localhost:8080")

    @pytest.fixture
    def mock_session(self, client):
        with patch.object(client._session, "request") as m:
            m.return_value = MagicMock(status_code=200)
            yield m

    def test_post_json_sets_content_type_and_json(self, client, mock_session):
        client.request("POST", "/api/resource", data={"key": "value"})
        assert mock_session.call_args[1]["json"] == {"key": "value"}
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_put_json_sets_content_type_and_json(self, client, mock_session):
        client.request("PUT", "/api/resource", data={"key": "value"})
        assert mock_session.call_args[1]["json"] == {"key": "value"}
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_patch_json_sets_content_type_and_json(self, client, mock_session):
        client.request("PATCH", "/api/resource", data={"key": "value"})
        assert mock_session.call_args[1]["json"] == {"key": "value"}
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_delete_with_dict_sends_json_body(self, client, mock_session):
        client.request("DELETE", "/api/resource", data={"id": ["xxx"]})
        assert mock_session.call_args[1]["json"] == {"id": ["xxx"]}
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_delete_with_list_sends_json_body(self, client, mock_session):
        client.request("DELETE", "/api/resource", data=[1, 2, 3])
        assert mock_session.call_args[1]["json"] == [1, 2, 3]
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_delete_without_data_does_not_set_json(self, client, mock_session):
        client.request("DELETE", "/api/resource")
        assert mock_session.call_args[1].get("json") is None
        headers = mock_session.call_args[1].get("headers")
        assert headers is None or "Content-Type" not in headers

    def test_get_with_dict_data_does_not_send_json(self, client, mock_session):
        client.request("GET", "/api/resource", data={"key": "value"})
        assert mock_session.call_args[1].get("json") is None

    def test_delete_with_files_uses_files_path(self, client, mock_session):
        client.request("DELETE", "/api/resource", data={"key": "value"}, files={"file": ("test.txt", b"data")})
        assert mock_session.call_args[1].get("json") is None
        assert mock_session.call_args[1]["files"] == {"file": ("test.txt", b"data")}

    def test_none_data_does_not_set_json(self, client, mock_session):
        client.request("DELETE", "/api/resource", data=None)
        assert mock_session.call_args[1].get("json") is None

    def test_post_with_string_body_sends_as_data(self, client, mock_session):
        client.request("POST", "/api/resource", data="<project/>")
        assert mock_session.call_args[1]["data"] == "<project/>"
        assert mock_session.call_args[1].get("json") is None

    def test_post_with_string_body_honors_user_content_type(self, client, mock_session):
        client.request("POST", "/api/resource", data="<project/>", headers={"Content-Type": "text/xml"})
        assert mock_session.call_args[1]["data"] == "<project/>"
        assert mock_session.call_args[1]["headers"]["Content-Type"] == "text/xml"

    def test_post_with_bytes_body_sends_as_data(self, client, mock_session):
        client.request("POST", "/api/resource", data=b"<project/>")
        assert mock_session.call_args[1]["data"] == b"<project/>"
        assert mock_session.call_args[1].get("json") is None


@patch("cliyard.client.http.requests.request")
class TestStandaloneRequest:
    """Tests for standalone ``request()`` — same logic as HttpClient."""

    def _make_ok(self, mock_req):
        mock_req.return_value = MagicMock(status_code=200)

    def test_delete_with_dict_sends_json_body(self, mock_req):
        self._make_ok(mock_req)
        request("DELETE", "http://localhost/api/resource", data={"id": ["xxx"]})
        assert mock_req.call_args[1]["json"] == {"id": ["xxx"]}
        assert mock_req.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_delete_with_list_sends_json_body(self, mock_req):
        self._make_ok(mock_req)
        request("DELETE", "http://localhost/api/resource", data=[1, 2, 3])
        assert mock_req.call_args[1]["json"] == [1, 2, 3]
        assert mock_req.call_args[1]["headers"]["Content-Type"] == "application/json"

    def test_delete_without_data_does_not_set_json(self, mock_req):
        self._make_ok(mock_req)
        request("DELETE", "http://localhost/api/resource")
        assert mock_req.call_args[1].get("json") is None

    def test_get_with_dict_data_does_not_send_json(self, mock_req):
        self._make_ok(mock_req)
        request("GET", "http://localhost/api/resource", data={"key": "value"})
        assert mock_req.call_args[1].get("json") is None

    def test_post_with_string_body_sends_as_data(self, mock_req):
        self._make_ok(mock_req)
        request("POST", "http://localhost/api/resource", data="<project/>")
        assert mock_req.call_args[1]["data"] == "<project/>"
        assert mock_req.call_args[1].get("json") is None

    def test_post_with_string_body_honors_user_content_type(self, mock_req):
        self._make_ok(mock_req)
        request("POST", "http://localhost/api/resource", data="<project/>", headers={"Content-Type": "text/xml"})
        assert mock_req.call_args[1]["data"] == "<project/>"
        assert mock_req.call_args[1]["headers"]["Content-Type"] == "text/xml"
