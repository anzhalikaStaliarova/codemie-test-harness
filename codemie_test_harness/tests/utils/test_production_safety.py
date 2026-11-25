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
from hamcrest import assert_that, contains_string, equal_to, is_, none, same_instance
import requests

from codemie_test_harness.tests.enums.environment import Environment
from codemie_test_harness.tests.utils import production_safety

pytestmark = pytest.mark.api


class FakeItem:
    def __init__(self, *markers: str):
        self._markers = set(markers)

    def get_closest_marker(self, name: str):
        return object() if name in self._markers else None

    def iter_markers(self, name: str | None = None):
        markers = self._markers if name is None else {name} & self._markers
        return [object() for _ in markers]


def _settings(monkeypatch, values: dict[str, str] | None = None) -> None:
    monkeypatch.setattr(
        production_safety.CredentialsManager,
        "get_parameter",
        staticmethod(lambda name, default="": (values or {}).get(name, default)),
    )
    monkeypatch.setattr(production_safety, "_read_only_run", False)
    monkeypatch.setattr(production_safety, "_run_mode_decided", False)
    monkeypatch.setattr(production_safety, "_collection_incomplete", False)
    monkeypatch.setattr(production_safety, "_guarded_tests", 0)


def _stand(monkeypatch, environment: Environment) -> None:
    monkeypatch.setattr(
        production_safety.EnvironmentResolver,
        "get_environment",
        classmethod(lambda cls: environment),
    )


def test_production_refuses_state_changes(monkeypatch):
    _settings(monkeypatch, {production_safety.ALLOW_STATE_CHANGES: "true"})
    _stand(monkeypatch, Environment.PRODUCTION)

    assert_that(
        production_safety.state_change_refusal(), equal_to("the stand is Production")
    )


@pytest.mark.parametrize(
    "environment", [Environment.PREVIEW, Environment.LOCALHOST, Environment.AWS]
)
def test_test_stands_allow_state_changes(monkeypatch, environment):
    _settings(monkeypatch)
    _stand(monkeypatch, environment)

    assert_that(production_safety.state_change_refusal(), none())


def test_unknown_stand_needs_explicit_permission(monkeypatch):
    _settings(monkeypatch)
    _stand(monkeypatch, Environment.UNKNOWN)
    assert_that(
        production_safety.state_change_refusal(),
        contains_string("not recognised as a test stand"),
    )

    _settings(monkeypatch, {production_safety.ALLOW_STATE_CHANGES: "true"})
    assert_that(production_safety.state_change_refusal(), none())


def test_read_only_run_refuses_state_changes_on_any_stand(monkeypatch):
    _settings(monkeypatch, {production_safety.READ_ONLY_RUN: "true"})
    _stand(monkeypatch, Environment.PREVIEW)

    assert_that(
        production_safety.state_change_refusal(), equal_to("the run is read-only")
    )


def test_unrelated_run_does_not_install_transport_guard(monkeypatch):
    _settings(monkeypatch)

    items = [FakeItem("ui")]

    assert_that(production_safety.transport_guard_required(items), same_instance(False))
    assert_that(production_safety.configure_run(items), same_instance(False))


def test_read_only_env_requires_transport_guard_without_cli_analytics(monkeypatch):
    _settings(monkeypatch, {production_safety.READ_ONLY_RUN: "true"})

    items = [FakeItem("ui")]

    assert_that(production_safety.transport_guard_required(items), same_instance(True))
    assert_that(production_safety.configure_run(items), same_instance(True))


def test_empty_selection_is_not_vacuously_read_only(monkeypatch):
    _settings(monkeypatch)

    assert_that(production_safety.transport_guard_required([]), same_instance(False))
    assert_that(production_safety.configure_run([]), same_instance(False))


def test_skipped_only_selection_is_not_vacuously_read_only(monkeypatch):
    _settings(monkeypatch)

    items = [FakeItem("cli_analytics", "prod_regression", "skip")]

    assert_that(production_safety.transport_guard_required(items), same_instance(True))
    assert_that(production_safety.configure_run(items), same_instance(False))


def test_all_runnable_read_only_cli_analytics_tests_require_guard(monkeypatch):
    _settings(monkeypatch)

    items = [FakeItem("cli_analytics", "prod_regression")]

    assert_that(production_safety.transport_guard_required(items), same_instance(True))
    assert_that(production_safety.configure_run(items), same_instance(True))


def test_state_changes_are_refused_for_read_only_item_in_mixed_run(monkeypatch):
    _settings(monkeypatch)

    assert_that(
        production_safety.state_changes_allowed(
            FakeItem("cli_analytics", "prod_regression")
        ),
        is_(False),
    )
    assert_that(production_safety.state_changes_allowed(FakeItem("api")), is_(True))


def test_state_changes_are_refused_while_read_only_test_is_guarded(monkeypatch):
    _settings(monkeypatch)
    item = FakeItem("cli_analytics", "prod_regression")

    with production_safety.read_only_test(item):
        assert_that(production_safety.state_changes_allowed(item), is_(False))
        assert_that(production_safety.state_changes_allowed(), is_(False))


def test_cleanup_fails_closed_on_xdist_controller_without_run_decision(monkeypatch):
    _settings(monkeypatch)

    assert_that(
        production_safety.session_cleanup_refusal(is_xdist_controller=True),
        equal_to("read-only safety mode was not finalized on the xdist controller"),
    )


def test_cleanup_is_skipped_for_decided_read_only_run(monkeypatch):
    _settings(monkeypatch)
    production_safety.configure_run([FakeItem("cli_analytics", "prod_regression")])

    assert_that(
        production_safety.session_cleanup_refusal(is_xdist_controller=False),
        equal_to("the run is read-only"),
    )


def test_cleanup_fails_closed_when_collection_is_incomplete(monkeypatch):
    _settings(monkeypatch)
    production_safety.mark_collection_incomplete()

    assert_that(
        production_safety.session_cleanup_refusal(is_xdist_controller=False),
        equal_to("collection was incomplete or failed"),
    )


def test_cleanup_is_allowed_after_writable_run_is_decided(monkeypatch):
    _settings(monkeypatch)
    production_safety.configure_run([FakeItem("api")])

    assert_that(
        production_safety.session_cleanup_refusal(is_xdist_controller=False), none()
    )


def test_is_forbidden_matches_equivalent_https_origins(monkeypatch):
    _settings(
        monkeypatch,
        {
            "CODEMIE_API_DOMAIN": "https://Codemie.Test:443/code-assistant-api",
            "FRONTEND_URL": "https://Ui.Codemie.Test:443",
        },
    )

    assert_that(
        production_safety.is_forbidden(
            "POST", "https://codemie.test/code-assistant-api/v1/projects"
        ),
        is_(True),
    )
    assert_that(
        production_safety.is_forbidden(
            "POST", "https://ui.codemie.test:443/v1/local-auth/login"
        ),
        is_(False),
    )


def test_is_forbidden_matches_equivalent_http_origins(monkeypatch):
    _settings(
        monkeypatch,
        {
            "CODEMIE_API_DOMAIN": "http://LOCALHOST:80/code-assistant-api",
            "FRONTEND_URL": "http://localhost:80",
        },
    )

    assert_that(
        production_safety.is_forbidden(
            "POST", "http://localhost/code-assistant-api/v1/projects"
        ),
        is_(True),
    )


def test_is_forbidden_normalizes_trailing_dot_in_host(monkeypatch):
    _settings(
        monkeypatch,
        {"CODEMIE_API_DOMAIN": "https://codemie.test./code-assistant-api"},
    )

    assert_that(
        production_safety.is_forbidden(
            "POST", "https://codemie.test/code-assistant-api/v1/projects"
        ),
        is_(True),
    )


def test_is_forbidden_handles_equivalent_base_path_variants(monkeypatch):
    _settings(
        monkeypatch,
        {
            "CODEMIE_API_DOMAIN": "https://codemie.test/gateway/code-assistant-api/",
            "FRONTEND_URL": "https://codemie.test/gateway",
        },
    )

    assert_that(
        production_safety.is_forbidden(
            "POST", "https://codemie.test/gateway/code-assistant-api/v1/projects"
        ),
        is_(True),
    )
    assert_that(
        production_safety.is_forbidden(
            "POST",
            "https://codemie.test/gateway/code-assistant-api/v1/local-auth/login",
        ),
        is_(False),
    )


def test_read_only_run_rejects_state_changing_http_request(monkeypatch):
    _settings(
        monkeypatch,
        {
            production_safety.READ_ONLY_RUN: "true",
            "CODEMIE_API_DOMAIN": "https://codemie.test/code-assistant-api",
            "FRONTEND_URL": "https://codemie.test",
        },
    )
    monkeypatch.setattr(production_safety, "_transport_guard_installed", False)
    monkeypatch.setattr(
        production_safety.requests.Session, "send", production_safety._original_send
    )
    production_safety.configure_run([FakeItem("ui")])
    production_safety.install_transport_guard()
    request = requests.Request(
        "POST", "https://codemie.test/code-assistant-api/v1/projects"
    ).prepare()

    with pytest.raises(production_safety.ReadOnlyViolation):
        requests.Session().send(request)
