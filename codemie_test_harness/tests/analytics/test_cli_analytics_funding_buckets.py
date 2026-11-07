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

"""CLI Analytics funding estimate buckets and the incomplete-price flag.

The aggregate responses add `funding` ({api_based, subscription, unknown}, a client-asserted
estimate by the session's first claim) and `personal_billed` (by billing class) beside the spend,
each bucket {cost_usd, session_count, cost_incomplete, unpriced_models}, and `metadata` adds
`cost_incomplete`. The seeds are legacy sessions sent as the harness user: they carry no funding
claim (read as unknown) and are not Personal-paid, so their whole estimate is the unknown bucket
and equals the spend, with tokens priced at the catalog price.
"""

import secrets
from datetime import timedelta

import pytest
from hamcrest import assert_that, close_to, equal_to, has_entries

from codemie_test_harness.tests.test_data.cli_analytics_test_data import (
    COST_TOLERANCE,
    CliSessionSeed,
    catalog_cost,
    day_window,
    seed_day_start,
    session_catalog_cost,
)
from codemie_test_harness.tests.utils.cli_publication_utils import (
    EMPTY_BUCKET,
    assert_buckets_shape,
    funding_cost_total,
    funding_session_total,
    read_endpoint,
)

AGGREGATES = ("overview", "cost", "users", "repositories", "efficiency")
# Entity rows of a response that carry their own buckets.
ENTITY_ROWS = {
    "cost": ("cost_by_user", "cost_by_model"),
    "users": ("rows",),
    "repositories": ("rows",),
}
UNPRICED_MODEL = f"autotest-unpriced-model-{secrets.token_hex(3)}"


def _seed(key: str, hour: int, tokens: int, model: str, repository: str):
    return CliSessionSeed(
        key=key,
        started_at=seed_day_start() + timedelta(hours=hour),
        input_tokens=tokens,
        output_tokens=tokens // 100,
        model=model,
        repository=repository,
    )


@pytest.fixture(scope="module")
def seed_as_harness_user(analytics_utils):
    """Ingest seeds as the harness user (a global admin may send to any enabled project) and
    wait until each is rolled up with its tokens."""

    def _seed_all(project: str, seeds: list[CliSessionSeed]) -> list[CliSessionSeed]:
        for seed in seeds:
            analytics_utils.ingest_cli_event_hooks(
                seed.events(project), project=project
            )
        for seed in seeds:
            analytics_utils.wait_for_cli_session(seed.session_id)
        return seeds

    return _seed_all


@pytest.fixture(scope="module")
def priced_sessions(create_cli_analytics_project, seed_as_harness_user, cost_model):
    """A project with two sessions of a catalog-priced model in two repositories."""
    project = create_cli_analytics_project()
    model = cost_model.base_name
    seeds = [
        _seed("first", 10, 4000, model, "/work/autotest-funding-a"),
        _seed("second", 11, 8000, model, "/work/autotest-funding-b"),
    ]
    return project, seed_as_harness_user(project, seeds)


@pytest.fixture(scope="module")
def mixed_sessions(create_cli_analytics_project, seed_as_harness_user, cost_model):
    """A project with one priced session and one of a model the catalog does not price."""
    project = create_cli_analytics_project()
    seeds = [
        _seed("priced", 10, 2000, cost_model.base_name, "/work/autotest-funding-c"),
        _seed("unpriced", 11, 16000, UNPRICED_MODEL, "/work/autotest-funding-c"),
    ]
    return project, seed_as_harness_user(project, seeds)


def _read(client, endpoint: str, project: str) -> dict:
    return read_endpoint(
        client, endpoint, {"projects": project, **day_window().params()}
    )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_legacy_sessions_are_an_unknown_funding_estimate_equal_to_spend(
    client, cost_model, priced_sessions
):
    """AC5/AC6: sessions without a funding claim are counted in the unknown bucket, never as
    API-based or subscription; nothing is Personal-paid, so the estimate equals the spend."""
    project, seeds = priced_sessions
    body = _read(client, "cost", project)
    data, metadata = body["data"], body["metadata"]
    expected = catalog_cost(seeds, cost_model.cost)

    assert_buckets_shape(data, "cost")
    assert_that(data["funding"]["api_based"], equal_to(EMPTY_BUCKET))
    assert_that(data["funding"]["subscription"], equal_to(EMPTY_BUCKET))
    assert_that(
        data["funding"]["unknown"],
        has_entries(
            {
                "cost_usd": close_to(expected, COST_TOLERANCE),
                "session_count": len(seeds),
                "cost_incomplete": False,
                "unpriced_models": [],
            }
        ),
    )
    assert_that(data["personal_billed"], equal_to(EMPTY_BUCKET))
    assert_that(data["kpis"]["total_cost_usd"], close_to(expected, COST_TOLERANCE))
    assert_that(
        data["kpis"]["total_cost_usd"] + data["personal_billed"]["cost_usd"],
        close_to(funding_cost_total(data), COST_TOLERANCE),
        "spend + personal-billed estimate must equal the funding estimate",
    )
    assert_that(data["models_without_spend"], equal_to([]))
    assert_that(
        metadata, has_entries({"cost_incomplete": False, "unpriced_models": []})
    )


@pytest.mark.cli_analytics
@pytest.mark.api
@pytest.mark.parametrize("endpoint", AGGREGATES)
def test_funding_buckets_agree_across_endpoints_and_entities(
    client, cost_model, priced_sessions, endpoint
):
    """AC5: every aggregate reports the same project-level buckets, and the per-user,
    per-repository and per-model rows partition them: each session once, its cost once."""
    project, seeds = priced_sessions
    expected = catalog_cost(seeds, cost_model.cost)
    data = _read(client, endpoint, project)["data"]

    assert_buckets_shape(data, endpoint)
    assert_that(funding_session_total(data), equal_to(len(seeds)))
    assert_that(funding_cost_total(data), close_to(expected, COST_TOLERANCE))
    assert_that(data["personal_billed"], equal_to(EMPTY_BUCKET))

    for field in ENTITY_ROWS.get(endpoint, ()):
        rows = data[field]
        for row in rows:
            assert_buckets_shape(row, f"{endpoint}.{field} row")
        assert_that(
            sum(funding_session_total(row) for row in rows),
            equal_to(len(seeds)),
            f"{endpoint}.{field}: sessions of the rows",
        )
        assert_that(
            sum(funding_cost_total(row) for row in rows),
            close_to(expected, COST_TOLERANCE),
            f"{endpoint}.{field}: estimate of the rows",
        )
    if endpoint == "repositories":
        by_repository = {
            row["repository"]: row["funding"]["unknown"] for row in data["rows"]
        }
        assert_that(
            {name: bucket["session_count"] for name, bucket in by_repository.items()},
            equal_to({seed.repository: 1 for seed in seeds}),
        )
        for seed in seeds:
            assert_that(
                by_repository[seed.repository]["cost_usd"],
                close_to(session_catalog_cost(seed, cost_model.cost), COST_TOLERANCE),
            )


@pytest.mark.cli_analytics
@pytest.mark.api
def test_unpriced_model_is_reported_as_incomplete_not_free(
    client, cost_model, mixed_sessions
):
    """AC7: a model without a catalog price makes the estimate and the spend incomplete, names the
    model, and keeps the priced part as it is; the unpriced session is never shown as costing 0."""
    project, seeds = mixed_sessions
    priced, unpriced = seeds
    priced_cost = session_catalog_cost(priced, cost_model.cost)
    body = _read(client, "cost", project)
    data, metadata = body["data"], body["metadata"]

    assert_that(
        metadata,
        has_entries({"cost_incomplete": True, "unpriced_models": [UNPRICED_MODEL]}),
    )
    assert_that(
        data["funding"]["unknown"],
        has_entries(
            {
                "cost_usd": close_to(priced_cost, COST_TOLERANCE),
                "session_count": 2,
                "cost_incomplete": True,
                "unpriced_models": [UNPRICED_MODEL],
            }
        ),
    )
    assert_that(data["funding"]["api_based"], equal_to(EMPTY_BUCKET))
    assert_that(data["kpis"]["total_cost_usd"], close_to(priced_cost, COST_TOLERANCE))

    sessions = read_endpoint(
        client, "sessions", {"projects": project, **day_window().params()}
    )
    assert_that(sessions["metadata"]["cost_incomplete"], equal_to(True))
    rows = {row["trace_id"]: row for row in sessions["data"]["sessions"]}
    assert_that(rows[priced.session_id]["cost_available"], equal_to(True))
    assert_that(
        rows[unpriced.session_id]["cost_available"],
        equal_to(False),
        "an unpriced session must not read as a free one",
    )

    detail = read_endpoint(client, f"sessions/{unpriced.session_id}", {})
    assert_that(
        detail["metadata"],
        has_entries({"cost_incomplete": True, "unpriced_models": [UNPRICED_MODEL]}),
    )
