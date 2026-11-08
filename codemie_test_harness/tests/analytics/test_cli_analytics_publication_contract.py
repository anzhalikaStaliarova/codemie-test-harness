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

"""CLI Analytics publication contract on the four ingest routes.

The contract headers are checked before the body is read, on every route: a malformed set is a
final 422 whatever the rollout switch (the parser runs first); a valid set is held with 503
`publication_contract_unavailable` while the operator switch is off, and is routed (explicit
folder consent to exactly the named project, Personal-paid or unknown without consent to private
storage) while it is on. Refusals carry `{"reason", "disposition"}` and never the publication echo.

The switch is read from the classification policy (`publication_contracts`); the tests of the other
state skip, the switch-on ones as BLOCKED. Every request is sent as the harness user.

Evidence limits: HTTP answers are authoritative; absence of a held session is black-box evidence
over a bounded window (observe_absence_window).
"""

import time
import uuid

import pytest
import requests
from hamcrest import (
    assert_that,
    close_to,
    equal_to,
    greater_than,
    has_entries,
    is_not,
    none,
)

from codemie_test_harness.tests.test_data.cli_analytics_test_data import (
    ADVERTISED,
    CONTRACT_UNAVAILABLE,
    COST_TOLERANCE,
    E1,
    E7,
    MALFORMED_CONTRACT_PARAMS,
    MAX_BODY_BYTES,
    NO_CORPORATE_ORGANIZATION,
    NOT_ADVERTISED,
    ROUND_TOKENS,
    ROUTE_PARAMS,
    VALID_CONTRACT_SHAPES,
    malformed_body,
)
from codemie_test_harness.tests.utils.base_utils import get_random_name
from codemie_test_harness.tests.utils.cli_analytics_api_utils import (
    CliAnalyticsApi,
    IngestClient,
    delete_projects_strict,
    wait_until_accessible,
)
from codemie_test_harness.tests.utils.cli_gate_utils import (
    make_batch,
    observe_absence_window,
)
from codemie_test_harness.tests.utils.cli_publication_utils import (
    BOUND_PROJECT_HEADER,
    CONTRACT_HEADER,
    DESTINATION_HEADER,
    assert_buckets_shape,
    assert_hooks_accepted_without_echo,
    assert_hooks_discarded,
    assert_publication_rejected,
    contract_headers,
    contracts_advertised,
    corporate_organization,
    echo_of,
    evidence_headers,
    own_personal_rows,
    read_endpoint,
    read_policy,
    send_hook_round,
    session_rows,
    wait_for_session_row,
)
from codemie_test_harness.tests.test_data.projects_test_data import (
    get_project_create_data,
)

WINDOW = {"time_period": "last_24_hours"}


def _session() -> tuple[str, str]:
    session_id = f"autotest-contract-{uuid.uuid4().hex[:12]}"
    return session_id, f"autotest/{session_id}"


def _post(client, combo, body, project, headers):
    return IngestClient(client).post_signal(
        combo.signal, body, combo.ct, project=project, extra_headers=headers
    )


def _post_refused_upload(client, combo, body, project, headers, attempts: int = 3):
    """`_post` for a body the stand refuses before reading it. The stand may answer and close the
    connection while the upload is still being written, which breaks reading the short answer under
    load; the status is decided before the body, so the request is sent again."""
    for attempt in range(1, attempts + 1):
        try:
            return _post(client, combo, body, project, headers)
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
        ):
            if attempt == attempts:
                raise


@pytest.fixture(scope="module")
def policy(client, cli_analytics_enabled):
    return read_policy(client)


@pytest.fixture(scope="module")
def switch_off(policy):
    if contracts_advertised(policy):
        pytest.skip(ADVERTISED)


@pytest.fixture(scope="module")
def switch_on(policy):
    if not contracts_advertised(policy):
        pytest.skip(NOT_ADVERTISED)


@pytest.fixture(scope="module")
def contract_project(client, create_cli_analytics_project):
    name = create_cli_analytics_project()
    wait_until_accessible(client, [name])
    return name


# ── Capability advertisement ────────────────────────────────────────────────────


@pytest.mark.cli_analytics
@pytest.mark.api
def test_policy_advertises_contracts_as_an_optional_list(policy):
    """AC9: the policy keeps its existing keys; `publication_contracts` is either absent (the
    switch is off, never null or empty) or exactly ["1"]."""
    assert_that(
        policy,
        has_entries(
            {
                "organizations": is_not(none()),
                "version": is_not(none()),
                "organization_encodings": equal_to(["utf8-b64u.v1"]),
            }
        ),
    )
    assert_that(isinstance(policy["organizations"], list), equal_to(True))
    assert_that(
        policy.get("publication_contracts", "absent"),
        is_not(equal_to(None)),
        "the key is omitted, not null, when the switch is off",
    )
    if "publication_contracts" in policy:
        assert_that(policy["publication_contracts"], equal_to(["1"]))


# ── Pre-body checks, whatever the switch ────────────────────────────────────────


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("case, combo", MALFORMED_CONTRACT_PARAMS)
def test_malformed_contract_is_refused_before_the_body_is_read(
    client, cli_analytics_enabled, case, combo
):
    """AC-neg: a malformed contract, consent or funding header set is a final 422 naming the
    defect, never a grant. The body is undecodable on purpose: a 422 instead of the decoder's 400
    shows the check ran before the body was read."""
    response = _post(client, combo, malformed_body(combo), case.project, case.headers)
    assert_publication_rejected(response, 422, case.reason, "final")


@pytest.mark.cli_analytics
@pytest.mark.api
def test_contract_check_precedes_the_content_type_check(client, cli_analytics_enabled):
    """The hook route answers a bad content type with 415 for a legacy request, but a malformed
    contract request with its 422 first."""
    legacy = _post(client, E7, b"x\n", "autotest", {"Content-Type": "text/plain"})
    assert_that(legacy.status_code, equal_to(415), f"precondition: {legacy.text[:200]}")
    response = _post(
        client,
        E7,
        b"x\n",
        "autotest",
        {"Content-Type": "text/plain", CONTRACT_HEADER: "2"},
    )
    assert_publication_rejected(
        response, 422, "publication_contract_unsupported", "final"
    )


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("combo", [E1, E7], ids=["logs", "event-hooks"])
def test_contract_check_precedes_the_body_size_limit(
    client, cli_analytics_enabled, combo
):
    """A body over the ingest size cap is a 413 for a legacy request; with a malformed contract it
    is the 422, so the oversized body was never accepted for reading."""
    oversized = b"0" * (MAX_BODY_BYTES + 1)
    legacy = _post_refused_upload(client, combo, oversized, "autotest", {})
    assert_that(
        legacy.status_code,
        equal_to(413),
        "precondition: the stand's ANALYTICS_INGEST_MAX_BODY_BYTES is the default 5 MiB",
    )
    response = _post_refused_upload(
        client,
        combo,
        oversized,
        "autotest",
        {CONTRACT_HEADER: "1", "X-CodeMie-Work-Consent": "x"},
    )
    assert_publication_rejected(response, 422, "consent_invalid", "final")


# ── Switch off: a valid contract is held, and nothing is stored ─────────────────


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("consent", VALID_CONTRACT_SHAPES)
@pytest.mark.parametrize("combo", ROUTE_PARAMS)
def test_valid_contract_is_held_while_the_switch_is_off(
    client, switch_off, consent, combo
):
    """AC-rollout: while the backend does not advertise the contract, a valid N0 or N1 request is
    held (503, disposition hold) on every route, before its body is read; the client keeps it."""
    session_id, _ = _session()
    headers = {
        "X-CodeMie-Session-Id": session_id,
        **contract_headers(consent=consent, funding="subscription"),
    }
    response = _post(client, combo, malformed_body(combo), "autotest", headers)
    assert_publication_rejected(response, 503, CONTRACT_UNAVAILABLE, "hold")


@pytest.mark.cli_analytics
@pytest.mark.api
def test_held_contract_sessions_are_never_stored(
    client, switch_off, policy, contract_project
):
    """AC-rollout/AC9: held N0 and N1 hook sessions to an enabled project are never listed, while a
    legacy evidence session of the same shape, sent last, is (the marker)."""
    project = contract_project
    leaks = {}
    for name, consent, cls in (
        ("N1-consent", True, "corporate"),
        ("N0-unknown", False, "unknown"),
        ("N0-codemie", False, "codemie"),
    ):
        if cls == "corporate" and corporate_organization(policy) is None:
            cls = "codemie"
        batch = make_batch(E7)
        headers = {
            **evidence_headers(batch.session_id, policy, cls),
            **contract_headers(consent=consent, funding="api_based"),
        }
        response = _post(client, E7, batch.body, project, headers)
        assert_publication_rejected(response, 503, CONTRACT_UNAVAILABLE, "hold")
        leaks[name] = {batch.session_id: batch.repo}

    marker = make_batch(E7)
    response = IngestClient(client).post_signal(
        "event-hooks",
        marker.body,
        E7.ct,
        project=project,
        extra_headers=evidence_headers(marker.session_id, policy, "codemie"),
    )
    assert_hooks_accepted_without_echo(response, "legacy evidence marker")
    observe_absence_window(
        CliAnalyticsApi(client),
        {marker.session_id: marker.repo},
        leaks,
        time.monotonic(),
    )


# ── Switch on (BLOCKED where it is off) ─────────────────────────────────────────


@pytest.fixture(scope="module")
def disabled_project(client, switch_on, project_utils):
    name = get_project_create_data()["name"]
    project_utils.create_project(name=name, cli_analytics_enabled=False)
    try:
        wait_until_accessible(client, [name])
        yield name
    finally:
        delete_projects_strict(project_utils, [name])


@pytest.mark.cli_analytics
@pytest.mark.api
def test_contract_without_session_is_refused(client, switch_on):
    """A contract request without X-CodeMie-Session-Id is never taken by the legacy gate."""
    response = _post(client, E7, b"x\n", "autotest", contract_headers())
    assert_publication_rejected(
        response, 422, "publication_contract_requires_session", "final"
    )


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize(
    "target, reason",
    [
        pytest.param("unknown", "project_unavailable", id="unknown-project"),
        pytest.param(
            "disabled", "project_analytics_not_enabled", id="analytics-disabled-project"
        ),
    ],
)
def test_consent_to_an_unusable_project_is_held_without_fallback(
    client, switch_on, policy, disabled_project, target, reason
):
    """AC1/AC3: consent names exactly one project; when it cannot take the session the request
    is held (503) and nothing is stored in the personal, a linked or the default project."""
    project = get_random_name() if target == "unknown" else disabled_project
    session_id, repo = _session()
    headers = {
        **evidence_headers(session_id, policy, "codemie"),
        **contract_headers(consent=True, funding="api_based"),
    }
    response = send_hook_round(
        client, project, headers, session_id, repo, 1, ROUND_TOKENS[1]
    )
    assert_publication_rejected(response, 503, reason, "hold")
    assert_that(own_personal_rows(client, repo), equal_to({}))


@pytest.mark.cli_analytics
@pytest.mark.api
def test_consented_personal_session_is_published_to_the_named_project(
    client, switch_on, policy, contract_project
):
    """AC1/AC5/AC6/AC8: a Personal-paid session with explicit folder consent is stored under the
    canonical name of the named project (header in another case), echoed as such, and keeps its
    first funding claim; its estimate is in `personal_billed` and the subscription bucket, never in
    the project's spend. A later legacy request or one naming another project is discarded."""
    if corporate_organization(policy) is None:
        pytest.skip(NO_CORPORATE_ORGANIZATION)
    project = contract_project
    session_id, repo = _session()
    evidence = evidence_headers(session_id, policy, "personal")
    first = send_hook_round(
        client,
        project.upper(),
        {**evidence, **contract_headers(consent=True, funding="subscription")},
        session_id,
        repo,
        1,
        ROUND_TOKENS[1],
    )
    assert_that(first.status_code, equal_to(200), first.text[:300])
    assert_that(
        echo_of(first),
        equal_to(
            {
                CONTRACT_HEADER: "1",
                DESTINATION_HEADER: "project",
                BOUND_PROJECT_HEADER: project,
            }
        ),
    )
    second = send_hook_round(
        client,
        project,
        {**evidence, **contract_headers(consent=True, funding="api_based")},
        session_id,
        repo,
        2,
        ROUND_TOKENS[2],
    )
    assert_that(second.status_code, equal_to(200), second.text[:300])
    assert_hooks_discarded(
        send_hook_round(
            client, project, evidence, session_id, repo, 3, ROUND_TOKENS[3]
        ),
        "session_contract_conflict",
        "a legacy request on the contract session",
    )
    assert_hooks_discarded(
        send_hook_round(
            client,
            get_random_name(),
            {**evidence, **contract_headers(consent=True, funding="subscription")},
            session_id,
            repo,
            3,
            ROUND_TOKENS[3],
        ),
        "session_project_conflict",
        "consent naming another project",
    )

    row = wait_for_session_row(
        client,
        {"projects": project, "search": repo, **WINDOW},
        session_id,
        input_tokens=ROUND_TOKENS[1] + ROUND_TOKENS[2],
    )
    assert_that(row, is_not(none()), "rounds 1 and 2 only, under the named project")
    assert_that(
        row,
        has_entries(
            {"subscription_type": "personal", "project_origin": "linked_folder"}
        ),
    )
    assert_that(own_personal_rows(client, repo), equal_to({}))

    cost = read_endpoint(
        client, "cost", {"projects": project, "repositories": repo, **WINDOW}
    )["data"]
    assert_buckets_shape(cost, "cost of the consented session's project")
    personal = cost["personal_billed"]
    assert_that(personal["session_count"], equal_to(1))
    assert_that(personal["cost_usd"], greater_than(0.0))
    assert_that(cost["funding"]["subscription"]["session_count"], equal_to(1))
    assert_that(
        cost["funding"]["subscription"]["cost_usd"],
        close_to(personal["cost_usd"], COST_TOLERANCE),
    )
    assert_that(cost["funding"]["api_based"]["session_count"], equal_to(0))
    assert_that(
        cost["kpis"]["total_cost_usd"],
        close_to(0.0, COST_TOLERANCE),
        "AC6: the Personal-paid session is listed in the project but adds nothing to its spend",
    )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_new_contract_session_without_consent_stays_private(
    client, switch_on, policy, contract_project
):
    """AC2/AC3: a contract session without consent whose funding is unknown goes to its owner's
    personal storage (echo `personal`, no bound project), even when it names a work project."""
    session_id, repo = _session()
    response = send_hook_round(
        client,
        contract_project,
        {
            **evidence_headers(session_id, policy, "unknown"),
            **contract_headers(funding="subscription"),
        },
        session_id,
        repo,
        1,
        ROUND_TOKENS[1],
    )
    assert_that(response.status_code, equal_to(200), response.text[:300])
    assert_that(
        echo_of(response),
        equal_to({CONTRACT_HEADER: "1", DESTINATION_HEADER: "personal"}),
    )
    own = wait_for_session_row(
        client, {"personal_only": "true", "search": repo}, session_id
    )
    assert_that(own, is_not(none()), "the session never reached its owner")
    assert_that(
        own, has_entries({"subscription_type": "unknown", "project_origin": "personal"})
    )
    assert_that(
        session_rows(client, {"projects": contract_project, "search": repo, **WINDOW}),
        equal_to({}),
    )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_funding_claims_are_bucketed_and_contradictions_read_unknown(
    client, switch_on, policy, create_cli_analytics_project
):
    """AC5/AC-neg: with consent, a CodeMie-routed api_based claim is api_based, a non-routed
    subscription claim is subscription; a routed subscription claim, a missing and a forged claim
    are unknown. Each session is counted once in exactly one bucket."""
    if corporate_organization(policy) is None:
        pytest.skip(NO_CORPORATE_ORGANIZATION)
    project = create_cli_analytics_project()
    wait_until_accessible(client, [project])
    cases = (
        ("codemie", "api_based", "api_based"),
        ("corporate", "subscription", "subscription"),
        ("codemie", "subscription", "unknown"),
        ("corporate", None, "unknown"),
        ("corporate", "forged-route", "unknown"),
    )
    for cls, claim, _ in cases:
        session_id, repo = _session()
        response = send_hook_round(
            client,
            project,
            {
                **evidence_headers(session_id, policy, cls),
                **contract_headers(consent=True, funding=claim),
            },
            session_id,
            repo,
            1,
            ROUND_TOKENS[1],
        )
        assert_that(
            response.status_code, equal_to(200), f"{cls}/{claim}: {response.text[:300]}"
        )
        assert_that(
            wait_for_session_row(
                client, {"projects": project, "search": repo, **WINDOW}, session_id
            ),
            is_not(none()),
            f"{cls}/{claim} session never listed",
        )

    funding = read_endpoint(client, "cost", {"projects": project, **WINDOW})["data"][
        "funding"
    ]
    expected = {route: 0 for route in ("api_based", "subscription", "unknown")}
    for _, _, route in cases:
        expected[route] += 1
    assert_that(
        {route: bucket["session_count"] for route, bucket in funding.items()},
        equal_to(expected),
    )
