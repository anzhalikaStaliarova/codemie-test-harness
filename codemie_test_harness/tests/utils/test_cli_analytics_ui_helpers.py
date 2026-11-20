# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from codemie_test_harness.tests.ui.analytics.test_cli_analytics_api_consistency import (
    _currency,
    _duration,
    _session_cost,
    _session_tool_calls,
)
from codemie_test_harness.tests.ui.pageobject.analytics.cli_analytics_page import (
    CLIAnalyticsPage,
)


class FakeRequest:
    def __init__(self, method: str):
        self.method = method


class FakeResponse:
    def __init__(self, url: str, method: str = "GET"):
        self.url = url
        self.request = FakeRequest(method)


API = "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"


def _page() -> CLIAnalyticsPage:
    return object.__new__(CLIAnalyticsPage)


def _api_domain(monkeypatch, value: str = "https://codemie.test/code-assistant-api"):
    monkeypatch.setattr(
        "codemie_test_harness.tests.ui.pageobject.analytics.cli_analytics_page."
        "CredentialsManager.get_parameter",
        staticmethod(lambda name, default="": value),
    )


def test_currency_keeps_two_significant_digits_below_a_cent():
    assert _currency(0.003465) == "$0.0035"
    assert _currency(0.000012) == "$0.000012"
    assert _currency(0.005) == "$0.005"
    assert _currency(0.0099) == "$0.0099"
    assert _currency(0.01) == "$0.01"


def test_currency_formats_two_decimals_with_the_sign_after_the_dollar():
    assert _currency(0) == "$0.00"
    assert _currency(1234.5) == "$1,234.50"
    assert _currency(-0.0) == "$-0.00"
    assert _currency(-0.001) == "$-0.00"
    assert _currency(-0.5) == "$-0.50"
    assert _currency(-101) == "$-101.00"


def test_session_cost_marks_unknown_and_keeps_partial_amounts():
    priced = {"cost_available": True, "cost_incomplete": False, "unpriced_models": []}
    assert _session_cost(0.0623, priced) == "$0.06"
    assert _session_cost(None, priced) == "-"
    assert _session_cost(1.5, {**priced, "cost_available": False}) == "-"
    assert _session_cost(1.5, {**priced, "unpriced_models": ["m"]}) == "$1.50"
    assert _session_cost(None, {**priced, "cost_incomplete": True}) == "-"


def test_duration_uses_three_units_from_the_largest_one():
    assert _duration(None) == "—"
    assert _duration(0) == "—"
    assert _duration(999) == "999ms"
    assert _duration(42_079) == "42s"
    assert _duration(90_000) == "1min 30s"
    assert _duration(3_603_000) == "1h 3s"
    assert _duration((2 * 30 + 8) * 86_400_000) == "2m 1w 1d"


def _summary(**fields) -> dict:
    return {
        "summary_state": "final",
        "summary_metrics": ["tool_calls"],
        "tool_calls_success": 6,
        "tool_success_rate": 100.0,
        **fields,
    }


def test_session_tool_calls_report_the_summary_total_and_observed_outcomes():
    assert _session_tool_calls(_summary(tool_call_count=6, tool_calls_total=6)) == (
        "6",
        "100% ok",
    )
    assert _session_tool_calls(
        _summary(tool_call_count=5, tool_calls_total=None, tool_success_rate=80.0)
    ) == ("5", "Observed only · 80% ok")
    assert _session_tool_calls(
        _summary(tool_call_count=6, tool_calls_total=10, summary_state="provisional")
    ) == ("10", "Provisional · 6 observed of 10 total · 100% ok")
    assert _session_tool_calls(_summary(tool_call_count=0, tool_calls_total=None)) == (
        "—",
        "Not reported",
    )
    assert _session_tool_calls(_summary(tool_call_count=0, tool_calls_total=4)) == (
        "4",
        "No outcomes observed",
    )


def test_overview_api_spend_request_is_not_the_overview_data(monkeypatch):
    _api_domain(monkeypatch)
    response = FakeResponse(f"{API}overview?subscription_type=codemie&time_period=x")

    assert not _page()._is_api_response(response, "overview", None)
    assert not _page()._is_api_response(response, "overview", {"time_period": "x"})
    assert _page()._is_api_response(
        response, "overview", {"subscription_type": "codemie"}
    )


def test_user_count_request_is_not_the_users_table(monkeypatch):
    _api_domain(monkeypatch)
    count = FakeResponse(f"{API}users?page=0&per_page=1")
    table = FakeResponse(f"{API}users?page=0&per_page=10")

    assert not _page()._is_api_response(count, "users", None)
    assert _page()._is_api_response(count, "users", {"per_page": 1})
    assert _page()._is_api_response(table, "users", None)


def test_framework_sessions_request_matches_without_default_page(monkeypatch):
    _api_domain(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert _page()._is_api_response(response, "sessions", {"framework": "claude_code"})


def test_framework_sessions_request_rejects_unrelated_endpoint(monkeypatch):
    _api_domain(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "users?framework=claude_code"
    )

    assert not _page()._is_api_response(
        response, "sessions", {"framework": "claude_code"}
    )


def test_framework_sessions_request_accepts_optional_single_path_prefix(monkeypatch):
    _api_domain(monkeypatch, "https://codemie.test/gateway/code-assistant-api")
    response = FakeResponse(
        "https://codemie.test/gateway/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert _page()._is_api_response(response, "sessions", {"framework": "claude_code"})


def test_framework_sessions_request_rejects_unrelated_prefix(monkeypatch):
    _api_domain(monkeypatch, "https://codemie.test/code-assistant-api")
    response = FakeResponse(
        "https://codemie.test/other/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert not _page()._is_api_response(
        response, "sessions", {"framework": "claude_code"}
    )


def test_framework_sessions_request_rejects_path_with_endpoint_suffix_only(monkeypatch):
    _api_domain(monkeypatch, "")
    response = FakeResponse(
        "https://codemie.test/unrelated/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert not _page()._is_api_response(
        response, "sessions", {"framework": "claude_code"}
    )


def test_framework_sessions_request_rejects_wrong_method(monkeypatch):
    _api_domain(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code",
        method="POST",
    )

    assert not _page()._is_api_response(
        response, "sessions", {"framework": "claude_code"}
    )


def test_framework_sessions_request_rejects_wrong_framework(monkeypatch):
    _api_domain(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=codex"
    )

    assert not _page()._is_api_response(
        response, "sessions", {"framework": "claude_code"}
    )
