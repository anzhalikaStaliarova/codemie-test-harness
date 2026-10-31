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

"""Production release smoke: the seeded CLI session's API totals reconcile with its
repository and user aggregates. Read-only (GET only); no writes to Production."""

import pytest
from hamcrest import assert_that, close_to, equal_to, greater_than, not_none

COST_TOLERANCE = 0.01


@pytest.mark.cli_analytics
@pytest.mark.api
def test_cli_analytics_production_smoke_session_totals_reconcile(
    analytics_utils, production_smoke_session
):
    # @case execute-repeatable-production-release-smoke_TC_004
    """Tokens, cost and duration of the Production smoke session reconcile across the
    session detail, the repository aggregate and the user aggregate for its Run ID.

    Turn/tool-call counts are not exposed by the analytics client's session models and
    are not asserted here (VERIFY: exact field for turns/tool-calls once grounded).
    """
    session = analytics_utils.find_cli_analytics_session(
        production_smoke_session.run_id
    )
    assert_that(
        session,
        not_none(),
        f"Production smoke session {production_smoke_session.run_id!r} was not found "
        "by GET /v1/analytics/cli-analytics/sessions/{trace_id}",
    )

    total_tokens = session.input_tokens + session.output_tokens
    assert_that(
        total_tokens,
        equal_to(production_smoke_session.total_tokens),
        "session token total should match the expected smoke session metadata",
    )
    assert_that(
        session.cost_usd,
        close_to(production_smoke_session.cost_usd, COST_TOLERANCE),
        "session cost should match the expected smoke session metadata",
    )
    assert_that(
        session.duration_ms,
        greater_than(0),
        "session duration should be reported for a completed smoke session",
    )

    repositories = analytics_utils.get_cli_analytics_repositories(
        repositories=session.repository
    ).data.rows
    repo_row = next((r for r in repositories if r.branch == session.branch), None)
    assert_that(
        repo_row,
        not_none(),
        f"repository aggregate has no row for {session.repository!r}@{session.branch!r}",
    )
    assert_that(
        repo_row.cost_usd,
        close_to(session.cost_usd, COST_TOLERANCE),
        "repository aggregate cost should reconcile with the session's own cost (the "
        "smoke repository/branch is expected to carry exactly this one session)",
    )

    users = analytics_utils.get_cli_analytics_users(
        repositories=session.repository
    ).data.rows
    user_row = next(
        (u for u in users if u.developer_name == session.developer_name), None
    )
    assert_that(
        user_row,
        not_none(),
        f"user aggregate has no row for {session.developer_name!r}",
    )
    assert_that(
        user_row.cost_usd,
        close_to(session.cost_usd, COST_TOLERANCE),
        "user aggregate cost (filtered to the smoke session's repository) should "
        "reconcile with the session's own cost",
    )
