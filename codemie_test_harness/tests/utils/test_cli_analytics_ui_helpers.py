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

import pytest
from hamcrest import assert_that, equal_to, is_

from codemie_test_harness.tests.ui.analytics.cli_analytics_number_format import (
    _currency,
    _locale,
)
from codemie_test_harness.tests.ui.pageobject.analytics.cli_analytics_page import (
    CLIAnalyticsPage,
)

pytestmark = pytest.mark.api

API = "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"


class FakeRequest:
    def __init__(self, method: str):
        self.method = method


class FakeResponse:
    def __init__(self, url: str, method: str = "GET"):
        self.url = url
        self.request = FakeRequest(method)


def _page() -> CLIAnalyticsPage:
    return object.__new__(CLIAnalyticsPage)


def _domains(
    monkeypatch,
    api_domain: str = "https://codemie.test/code-assistant-api",
    frontend_url: str = "",
):
    values = {
        "CODEMIE_API_DOMAIN": api_domain,
        "FRONTEND_URL": frontend_url,
    }
    monkeypatch.setattr(
        "codemie_test_harness.tests.ui.pageobject.analytics.cli_analytics_page."
        "CredentialsManager.get_parameter",
        staticmethod(lambda name, default="": values.get(name, default)),
    )


def test_locale_rounds_like_intl_shortest_decimal():
    assert_that(_locale(0.015, 2), equal_to("0.02"))
    assert_that(_locale(1.005, 2), equal_to("1.01"))
    assert_that(_locale(12.345, 2), equal_to("12.35"))


def test_currency_rounds_like_intl_shortest_decimal():
    assert_that(_currency(0.015), equal_to("$0.02"))
    assert_that(_currency(1.005), equal_to("$1.01"))
    assert_that(_currency(12.345), equal_to("$12.35"))


def test_overview_api_spend_request_is_not_the_overview_data(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(f"{API}overview?subscription_type=codemie&time_period=x")

    assert_that(_page()._is_api_response(response, "overview", None), is_(False))
    assert_that(
        _page()._is_api_response(response, "overview", {"time_period": "x"}), is_(False)
    )
    assert_that(
        _page()._is_api_response(
            response, "overview", {"subscription_type": "codemie"}
        ),
        is_(True),
    )


def test_user_count_request_is_not_the_users_table(monkeypatch):
    _domains(monkeypatch)
    count = FakeResponse(f"{API}users?page=0&per_page=1")
    table = FakeResponse(f"{API}users?page=0&per_page=10")

    assert_that(_page()._is_api_response(count, "users", None), is_(False))
    assert_that(_page()._is_api_response(count, "users", {"per_page": 1}), is_(True))
    assert_that(_page()._is_api_response(table, "users", None), is_(True))


def test_framework_sessions_request_matches_without_default_page(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(True),
    )


def test_framework_sessions_request_matches_equivalent_origin_forms(monkeypatch):
    _domains(monkeypatch, api_domain="https://CODemie.test:443/code-assistant-api")
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(True),
    )


def test_framework_sessions_request_rejects_unrelated_endpoint(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "users?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )


def test_framework_sessions_request_accepts_configured_path_prefix(monkeypatch):
    _domains(monkeypatch, "https://codemie.test/gateway/code-assistant-api")
    response = FakeResponse(
        "https://codemie.test/gateway/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(True),
    )


def test_framework_sessions_request_accepts_frontend_proxy_prefix(monkeypatch):
    _domains(
        monkeypatch,
        api_domain="https://codemie.test/code-assistant-api",
        frontend_url="https://codemie.test/gateway",
    )
    response = FakeResponse(
        "https://codemie.test/gateway/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(True),
    )


def test_framework_sessions_request_accepts_frontend_proxy_on_another_origin(
    monkeypatch,
):
    _domains(
        monkeypatch,
        api_domain="https://api.codemie.test/code-assistant-api",
        frontend_url="https://ui.codemie.test/gateway",
    )
    response = FakeResponse(
        "https://ui.codemie.test/gateway/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(True),
    )


def test_framework_sessions_request_rejects_unrelated_prefix(monkeypatch):
    _domains(monkeypatch, "https://codemie.test/code-assistant-api")
    response = FakeResponse(
        "https://codemie.test/other/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )


def test_framework_sessions_request_rejects_path_with_endpoint_suffix_only(monkeypatch):
    _domains(monkeypatch, api_domain="", frontend_url="")
    response = FakeResponse(
        "https://codemie.test/unrelated/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )


def test_framework_sessions_request_rejects_wrong_method(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code",
        method="POST",
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )


def test_framework_sessions_request_rejects_wrong_framework(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(
        "https://codemie.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=codex"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )


def test_framework_sessions_request_rejects_wrong_origin(monkeypatch):
    _domains(monkeypatch)
    response = FakeResponse(
        "https://other.test/code-assistant-api/v1/analytics/cli-analytics/"
        "sessions?framework=claude_code"
    )

    assert_that(
        _page()._is_api_response(response, "sessions", {"framework": "claude_code"}),
        is_(False),
    )
