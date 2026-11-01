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

"""UI coverage for the Analytics > CLI Analytics dashboard (tab opening is smoke)."""

import uuid

import pytest

from codemie_test_harness.tests.ui.pageobject.analytics.analytics_page import (
    AnalyticsPage,
)
from codemie_test_harness.tests.ui.pageobject.analytics.cli_analytics_page import (
    CLIAnalyticsPage,
)
from codemie_test_harness.tests.ui.test_data.analytics_test_data import (
    CLIAnalyticsSection,
)


@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.cli_analytics
def test_cli_analytics_tab_opens_from_analytics_page(page):
    """CLI Analytics tab opens from the Analytics page with its default section loaded."""
    cli_analytics = AnalyticsPage(page).navigate_to().open_cli_analytics()

    cli_analytics.should_have_sections()
    cli_analytics.should_have_loaded_section(CLIAnalyticsPage.DEFAULT_SECTION)


@pytest.mark.ui
@pytest.mark.cli_analytics
def test_cli_analytics_sections_load(page):
    """Every CLI Analytics section loads its data and renders without widget errors."""
    cli_analytics = CLIAnalyticsPage(page).navigate_to()
    cli_analytics.should_have_loaded_section(CLIAnalyticsPage.DEFAULT_SECTION)

    for section in CLIAnalyticsSection:
        if section == CLIAnalyticsPage.DEFAULT_SECTION:
            continue
        cli_analytics.open_section(section)
        cli_analytics.should_have_loaded_section(section)


@pytest.mark.ui
@pytest.mark.cli_analytics
def test_cli_analytics_session_opens_from_sessions_table(page, cli_analytics_session):
    """A seeded session is found in the Sessions table and opens its detail modal."""
    cli_analytics = CLIAnalyticsPage(page).navigate_to()
    cli_analytics.open_section(CLIAnalyticsSection.SESSIONS)
    cli_analytics.should_have_loaded_section(CLIAnalyticsSection.SESSIONS)

    cli_analytics.search_sessions(cli_analytics_session.repository)
    session_modal = cli_analytics.open_session(cli_analytics_session.repository)
    session_modal.should_have_title(cli_analytics_session.prompt)
    session_modal.should_have_session_details()

    session_modal.close()
    session_modal.should_be_closed()


@pytest.mark.ui
@pytest.mark.cli_analytics
def test_cli_analytics_session_found_by_run_id(page, cli_analytics_session):
    # @case execute-repeatable-production-release-smoke_TC_003
    """A seeded session is found by its Run ID (session id) with exact model/
    repository/branch, and an unrelated id lists no rows.

    The Sessions search box matches prompt, repository or branch, never the session id
    itself (CLIAnalyticsPage.sessions_search_input / the BE's `search` query param, see
    cli_analytics_handler.get_sessions); it still finds the session here because the
    fixture's repository literally embeds the Run ID (f"autotest/{session_id}"), so a
    search for the Run ID is a real substring match of that field, not a stand-in for one.

    The session's user/developer is not asserted: neither the Sessions table row nor
    this dialog renders it anywhere (codemie-ui SessionsView.tsx / SessionModal.tsx /
    SessionBody.tsx) -- it is only ever shown on the separate Users/Cost tabs,
    aggregated across sessions, see should_show_session_metadata.
    """
    cli_analytics = CLIAnalyticsPage(page).navigate_to()
    cli_analytics.open_section(CLIAnalyticsSection.SESSIONS)
    cli_analytics.should_have_loaded_section(CLIAnalyticsSection.SESSIONS)

    cli_analytics.search_sessions(cli_analytics_session.session_id)
    cli_analytics.should_list_exactly_one_session(cli_analytics_session.session_id)

    session_modal = cli_analytics.open_session(cli_analytics_session.session_id)
    session_modal.should_show_session_metadata(
        model=cli_analytics_session.model,
        repository=cli_analytics_session.repository,
        branch=cli_analytics_session.branch,
    )
    session_modal.close()
    session_modal.should_be_closed()

    unknown_run_id = f"autotest-cli-{uuid.uuid4().hex[:12]}"
    cli_analytics.search_sessions(unknown_run_id)
    cli_analytics.should_list_no_sessions()


@pytest.mark.cli_analytics
@pytest.mark.ui
def test_cli_analytics_tool_success_dash_when_no_tool_calls(
    page, cli_analytics_session
):
    # @case cli-analytics-tool-success-dash_TC_001
    """Tool Success shows '—' on Overview, Users row, and Users modal when tool_calls = 0."""
    cli_analytics = CLIAnalyticsPage(page).navigate_to()
    cli_analytics.filter_by_project(cli_analytics_session.project)
    cli_analytics.should_show_overview_tool_success_dash()

    cli_analytics.open_section(CLIAnalyticsSection.USERS)
    cli_analytics.should_show_first_user_row_tool_success_dash()

    user_modal = cli_analytics.open_first_user_modal()
    user_modal.should_show_tool_success_dash()
