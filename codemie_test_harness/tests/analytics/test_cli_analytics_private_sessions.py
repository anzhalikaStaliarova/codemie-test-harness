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

"""CLI Analytics private sessions on the evidence path every released CLI uses.

The evidence regime L1 stays unchanged (AC9): without the publication contract an
explicitly connected folder grants no consent, so a Personal-paid session of a user who can have a
personal project is owner-only, whatever project the request names. These tests send L1 sessions
as the harness user (a global admin with a personal project) and check what the work project and
the owner read, and that a session keeps its first destination and class across later batches
(/clear, resume and folder changes reach the backend as later batches of a session or a new session).

Evidence limits: the reads are black-box. An absence is proven after a later batch of the same
session was rolled up: ingest writes before it answers, so a batch sent earlier and stored would be
in that rollup too. The owner here is a global admin, so "not in the work project" holds even for the
broadest reader; other users' reads are covered by the Keycloak-provisioned test at the end.
"""

import uuid

import pytest
from hamcrest import assert_that, empty, equal_to, has_entries, is_not, none

from codemie_test_harness.tests.test_data.cli_analytics_test_data import (
    NO_CORPORATE_ORGANIZATION,
    ROUND_TOKENS,
)
from codemie_test_harness.tests.utils.cli_analytics_api_utils import (
    CliAnalyticsApi,
    wait_until_accessible,
)
from codemie_test_harness.tests.utils.cli_publication_utils import (
    EMPTY_BUCKET,
    LINKED_FOLDER,
    assert_buckets_shape,
    assert_hooks_accepted_without_echo,
    assert_hooks_discarded,
    corporate_organization,
    evidence_headers,
    funding_session_total,
    own_personal_rows,
    read_endpoint,
    read_policy,
    send_hook_round,
    session_rows,
    wait_for_session_row,
)

WORK_CLASSES = ("codemie", "corporate", "unknown")
WINDOW = {"time_period": "last_24_hours"}
AGGREGATES = ("overview", "cost", "users", "repositories", "efficiency")


def _session() -> tuple[str, str]:
    session_id = f"autotest-priv-{uuid.uuid4().hex[:12]}"
    return session_id, f"autotest/{session_id}"


@pytest.fixture(scope="module")
def personal_policy(client, cli_analytics_enabled):
    """The classification policy; skips where no session can be classified Personal."""
    policy = read_policy(client)
    if corporate_organization(policy) is None:
        pytest.skip(NO_CORPORATE_ORGANIZATION)
    return policy


@pytest.fixture(scope="module")
def work_projects(client, create_cli_analytics_project):
    """Two fresh enabled projects the harness user can send to (readable before the first send,
    so a lagging lookup cannot reroute a session to the default project)."""
    names = [create_cli_analytics_project(), create_cli_analytics_project()]
    wait_until_accessible(client, names)
    return names


@pytest.fixture(scope="module")
def connected_folder_sessions(client, personal_policy, work_projects):
    """One session per class from a folder connected to the first project (header + linked_folder
    source): {class: (session_id, repo)}, each waited for where its reader can see it."""
    project = work_projects[0]
    sessions = {}
    for cls in ("personal", *WORK_CLASSES):
        session_id, repo = _session()
        response = send_hook_round(
            client,
            project,
            evidence_headers(session_id, personal_policy, cls),
            session_id,
            repo,
            1,
            ROUND_TOKENS[1],
        )
        assert_hooks_accepted_without_echo(response, f"{cls} session")
        sessions[cls] = (session_id, repo)
    for cls in WORK_CLASSES:
        session_id, repo = sessions[cls]
        row = wait_for_session_row(
            client, {"projects": project, "search": repo, **WINDOW}, session_id
        )
        assert_that(row, is_not(none()), f"the {cls} session never reached {project}")
    session_id, repo = sessions["personal"]
    personal = poll_own(client, session_id, repo)
    assert_that(
        personal, is_not(none()), "the personal session never reached its owner"
    )
    return sessions


def poll_own(client, session_id: str, repo: str, input_tokens: int | None = None):
    return wait_for_session_row(
        client, {"personal_only": "true", "search": repo}, session_id, input_tokens
    )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_personal_session_from_a_connected_folder_is_not_published_to_the_project(
    client, work_projects, connected_folder_sessions
):
    """AC2/AC3/AC9: a legacy client's Personal-paid session from a folder connected to a work
    project stays in its owner's personal analytics; the work project lists the other classes."""
    project = work_projects[0]
    personal_id, personal_repo = connected_folder_sessions["personal"]

    listed = session_rows(client, {"projects": project, **WINDOW})
    assert_that(
        sorted(listed),
        equal_to(sorted(connected_folder_sessions[cls][0] for cls in WORK_CLASSES)),
        "the work project lists exactly its codemie, corporate and unknown sessions",
    )
    assert_that(
        session_rows(client, {"search": personal_repo, **WINDOW}),
        empty(),
        "outside personal analytics the private session is not listed, not even to its owner, "
        "a global admin",
    )
    own = own_personal_rows(client, personal_repo)
    assert_that(sorted(own), equal_to([personal_id]))
    assert_that(
        own[personal_id],
        has_entries({"subscription_type": "personal", "project_origin": "personal"}),
        "the session is stored in the owner's personal project, not the connected one",
    )


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("endpoint", AGGREGATES)
def test_work_project_aggregates_hold_only_the_work_sessions(
    client, work_projects, connected_folder_sessions, endpoint
):
    """AC2/AC6: the project's aggregates count its three work sessions once each, nothing
    Personal-paid, and every funding bucket is well formed; a filter that selects only the private
    session aggregates nothing, even for a global admin."""
    project = work_projects[0]
    payload = read_endpoint(client, endpoint, {"projects": project, **WINDOW})["data"]
    assert_buckets_shape(payload, f"{endpoint} of {project}")
    assert_that(funding_session_total(payload), equal_to(len(WORK_CLASSES)))
    assert_that(payload["personal_billed"], equal_to(EMPTY_BUCKET))

    _, personal_repo = connected_folder_sessions["personal"]
    private_only = read_endpoint(
        client, endpoint, {"repositories": personal_repo, **WINDOW}
    )["data"]
    assert_that(
        funding_session_total(private_only),
        equal_to(0),
        f"{endpoint}: the private session is counted in a shared aggregate",
    )
    assert_that(private_only["personal_billed"], equal_to(EMPTY_BUCKET))


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("cls", [*WORK_CLASSES, "personal"])
def test_subscription_class_filter_keeps_the_classes_apart(
    client, work_projects, connected_folder_sessions, cls
):
    """AC5: corporate, CodeMie and unknown sessions of one project stay distinguishable by the
    subscription type filter, each stored with the linked-folder origin; Personal selects nothing
    in the work project."""
    project = work_projects[0]
    rows = session_rows(
        client, {"projects": project, "subscription_type": cls, **WINDOW}
    )
    expected = [] if cls == "personal" else [connected_folder_sessions[cls][0]]
    assert_that(sorted(rows), equal_to(expected))
    for row in rows.values():
        assert_that(
            row,
            has_entries({"subscription_type": cls, "project_origin": LINKED_FOLDER}),
        )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_later_batch_from_another_folder_keeps_the_session_in_its_project(
    client, personal_policy, work_projects
):
    """AC8: a session that continues after a folder change (its next batch names another
    connected project) is neither reassigned nor duplicated: every round is stored once under the
    project of its first batch, and the other project holds nothing of it."""
    first, other = work_projects
    session_id, repo = _session()
    headers = evidence_headers(session_id, personal_policy, "codemie")
    assert_hooks_accepted_without_echo(
        send_hook_round(client, first, headers, session_id, repo, 1, ROUND_TOKENS[1]),
        "round 1",
    )
    assert_hooks_accepted_without_echo(
        send_hook_round(client, other, headers, session_id, repo, 2, ROUND_TOKENS[2]),
        "round 2 from the other folder",
    )
    # the first round again, as a resumed CLI resends an unacknowledged range
    assert_hooks_accepted_without_echo(
        send_hook_round(client, first, headers, session_id, repo, 1, ROUND_TOKENS[1]),
        "round 1 resent",
    )

    row = wait_for_session_row(
        client,
        {"projects": first, "search": repo, **WINDOW},
        session_id,
        input_tokens=ROUND_TOKENS[1] + ROUND_TOKENS[2],
    )
    assert_that(row, is_not(none()), "both rounds, once each, under the first project")
    assert_that(row, has_entries({"subscription_type": "codemie"}))
    assert_that(
        session_rows(client, {"projects": other, "search": repo, **WINDOW}), empty()
    )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_personal_batch_on_a_work_session_is_discarded(
    client, personal_policy, work_projects
):
    """AC8: when the funding of a running work session turns Personal (a profile switch), that
    batch is discarded with session_project_conflict; the session is not reclassified and the
    personal round never reaches the work project."""
    project = work_projects[0]
    session_id, repo = _session()
    work = evidence_headers(session_id, personal_policy, "codemie")
    personal = evidence_headers(session_id, personal_policy, "personal")
    assert_hooks_accepted_without_echo(
        send_hook_round(client, project, work, session_id, repo, 1, ROUND_TOKENS[1]),
        "work round 1",
    )
    assert_that(
        wait_for_session_row(
            client, {"projects": project, "search": repo, **WINDOW}, session_id
        ),
        is_not(none()),
        "the work session was never listed",
    )
    assert_hooks_discarded(
        send_hook_round(
            client, project, personal, session_id, repo, 2, ROUND_TOKENS[2]
        ),
        "session_project_conflict",
        "personal round 2",
    )
    assert_hooks_accepted_without_echo(
        send_hook_round(client, project, work, session_id, repo, 3, ROUND_TOKENS[3]),
        "work round 3",
    )

    row = wait_for_session_row(
        client,
        {"projects": project, "search": repo, **WINDOW},
        session_id,
        input_tokens=ROUND_TOKENS[1] + ROUND_TOKENS[3],
    )
    assert_that(row, is_not(none()), "rounds 1 and 3 only, the personal round never")
    assert_that(row, has_entries({"subscription_type": "codemie"}))
    assert_that(own_personal_rows(client, repo), empty())


@pytest.mark.cli_analytics
@pytest.mark.api
def test_work_batch_on_a_personal_session_keeps_it_private(
    client, personal_policy, work_projects
):
    """AC8/AC9: a later work-classified batch that names the connected project does not widen a
    private session: it is stored with the session in personal analytics, which stays Personal
    and out of the work project."""
    project = work_projects[0]
    session_id, repo = _session()
    assert_hooks_accepted_without_echo(
        send_hook_round(
            client,
            project,
            evidence_headers(session_id, personal_policy, "personal"),
            session_id,
            repo,
            1,
            ROUND_TOKENS[1],
        ),
        "personal round 1",
    )
    assert_hooks_accepted_without_echo(
        send_hook_round(
            client,
            project,
            evidence_headers(session_id, personal_policy, "corporate"),
            session_id,
            repo,
            2,
            ROUND_TOKENS[2],
        ),
        "corporate round 2",
    )

    row = poll_own(client, session_id, repo, ROUND_TOKENS[1] + ROUND_TOKENS[2])
    assert_that(row, is_not(none()), "both rounds stay with the private session")
    assert_that(
        row,
        has_entries({"subscription_type": "personal", "project_origin": "personal"}),
    )
    assert_that(
        session_rows(client, {"projects": project, "search": repo, **WINDOW}), empty()
    )


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.requires_setting("KEYCLOAK_ADMIN_USERNAME", "KEYCLOAK_KEY")
def test_personal_session_is_readable_by_its_owner_only(
    project_world, gate_member_client
):
    """AC3: a plain member's Personal-paid session from a folder connected to the project is
    listed to the member in personal analytics only; the project admin, a global admin and an
    unrelated user neither list it nor read its detail (404, no existence leak)."""
    policy = read_policy(gate_member_client)
    if corporate_organization(policy) is None:
        pytest.skip(NO_CORPORATE_ORGANIZATION)
    project = project_world.project
    session_id, repo = _session()
    assert_hooks_accepted_without_echo(
        send_hook_round(
            gate_member_client,
            project,
            evidence_headers(session_id, policy, "personal"),
            session_id,
            repo,
            1,
            ROUND_TOKENS[1],
        ),
        "member's personal session",
    )
    # a non-admin's /sessions is always its own Personal scope
    own = wait_for_session_row(
        gate_member_client, {"search": repo}, session_id, ROUND_TOKENS[1]
    )
    assert_that(own, is_not(none()), "the owner never saw the session")

    for role in ("project_admin", "admin", "unrelated"):
        reader = project_world.clients[role]
        assert_that(
            session_rows(reader, {"search": repo, **WINDOW}),
            empty(),
            f"{role} lists the member's private session",
        )
        detail = CliAnalyticsApi(reader).request(
            "GET", f"/v1/analytics/cli-analytics/sessions/{session_id}"
        )
        assert_that(
            detail.status_code,
            equal_to(404),
            f"{role} reads the member's private session detail: {detail.text[:200]}",
        )
