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

"""Publication contract pieces of the CLI Analytics tests: headers, rejections, reads.

The ingest regimes of the backend:
- L0 legacy: no evidence header, the X-CodeMie-Project gate;
- L1 evidence: X-CodeMie-Session-Id and the classification headers, no contract header;
- N0: L1 plus X-CodeMie-Publication-Contract: 1, without consent;
- N1: N0 plus X-CodeMie-Work-Consent: explicit-folder and a project.

The contract is behind an operator switch: the classification policy lists `publication_contracts`
only while it is on, and while it is off every contract request is held (503) before its body is read.

The SDK response models predate the funding buckets, so these helpers read the raw JSON.
"""

import secrets

from hamcrest import assert_that, empty, equal_to, has_entries

from codemie_test_harness.otlp_builder import build_hook_events, ndjson_bytes
from codemie_test_harness.tests.test_data.cli_analytics_test_data import (
    PRESENCE_POLL_S,
    PRESENCE_TIMEOUT_S,
)
from codemie_test_harness.tests.utils.base_utils import poll_until
from codemie_test_harness.tests.utils.cli_analytics_api_utils import (
    CT_NDJSON,
    INGEST_PREFIX,
    CliAnalyticsApi,
    IngestClient,
)

CONTRACT_HEADER = "X-CodeMie-Publication-Contract"
CONSENT_HEADER = "X-CodeMie-Work-Consent"
FUNDING_HEADER = "X-CodeMie-Funding-Route"
DESTINATION_HEADER = "X-CodeMie-Destination"
BOUND_PROJECT_HEADER = "X-CodeMie-Bound-Project"
SESSION_HEADER = "X-CodeMie-Session-Id"
ROUTED_HEADER = "X-CodeMie-Routed-Via-CodeMie"
ORGANIZATION_HEADER = "X-CodeMie-Organization"
SUBSCRIPTION_HEADER = "X-CodeMie-Subscription-Type"
POLICY_VERSION_HEADER = "X-CodeMie-Policy-Version"
PROJECT_SOURCE_HEADER = "X-CodeMie-Project-Source"
ECHO_HEADERS = (CONTRACT_HEADER, DESTINATION_HEADER, BOUND_PROJECT_HEADER)

CONTRACT_VERSION = "1"
CONSENT = "explicit-folder"
LINKED_FOLDER = "linked_folder"
POLICY_PATH = f"{INGEST_PREFIX}/classification-policy"

FUNDING_ROUTES = ("api_based", "subscription", "unknown")
EMPTY_BUCKET = {
    "cost_usd": 0.0,
    "session_count": 0,
    "cost_incomplete": False,
    "unpriced_models": [],
}
# An organization that is not on the corporate list: a non-routed session naming it is Personal-paid.
PERSONAL_ORGANIZATION = f"autotest-personal-org-{secrets.token_hex(4)}"


# ── Policy ──────────────────────────────────────────────────────────────────────


def read_policy(client) -> dict:
    response = CliAnalyticsApi(client).request("GET", POLICY_PATH)
    assert_that(
        response.status_code,
        equal_to(200),
        f"classification policy: {response.status_code} {response.text[:200]}",
    )
    return response.json()


def contracts_advertised(policy: dict) -> bool:
    """Whether the backend negotiates contract 1 (its rollout switch is on)."""
    return CONTRACT_VERSION in (policy.get("publication_contracts") or [])


def corporate_organization(policy: dict) -> str | None:
    """A configured corporate organization, or None: without one no session is Corporate or Personal
    (the classifier answers Unknown for every organization when the list is empty)."""
    return next(iter(policy.get("organizations") or []), None)


# ── Request headers ─────────────────────────────────────────────────────────────


def evidence_headers(
    session_id: str, policy: dict, cls: str, project_source: str = LINKED_FOLDER
) -> dict:
    """L1 headers of a session the backend classifies as `cls` (codemie | corporate | personal | unknown).

    The client states the same class and the current policy version, as a current CLI does, so the
    request is never answered with the stale-policy 409."""
    headers = {
        SESSION_HEADER: session_id,
        PROJECT_SOURCE_HEADER: project_source,
        SUBSCRIPTION_HEADER: cls,
        POLICY_VERSION_HEADER: policy["version"],
    }
    if cls == "codemie":
        headers[ROUTED_HEADER] = "true"
    elif cls == "corporate":
        headers[ORGANIZATION_HEADER] = corporate_organization(policy)
    elif cls == "personal":
        headers[ORGANIZATION_HEADER] = PERSONAL_ORGANIZATION
    return headers


def contract_headers(consent: bool = False, funding: str | None = None) -> dict:
    """The N0 (or, with consent, N1) headers added to evidence headers."""
    headers = {CONTRACT_HEADER: CONTRACT_VERSION}
    if consent:
        headers[CONSENT_HEADER] = CONSENT
    if funding is not None:
        headers[FUNDING_HEADER] = funding
    return headers


# ── Hook sessions sent in rounds ────────────────────────────────────────────────


def hook_round(
    session_id: str, repo: str, round_no: int, input_tokens: int, prompt: str
) -> bytes:
    """One batch of a hook session: round 1 starts it, every round adds a prompt and a usage
    request with its own event ids and `input_tokens`, so the session's token total tells which
    rounds were stored (a round that is stored twice or never changes it)."""
    events = build_hook_events(session_id, None, "autotest", repo, prompt)
    start, submit, usage, _end = events
    kept = [start, submit, usage] if round_no == 1 else [submit, usage]
    for event in kept:
        event["event_id"] = f"{event['event_id']}:round-{round_no}"
    usage["request_id"] = f"req-{session_id}-round-{round_no}"
    usage["input_tokens"] = input_tokens
    return ndjson_bytes(kept)


def send_hook_round(
    client,
    project: str | None,
    headers: dict,
    session_id: str,
    repo: str,
    round_no: int,
    input_tokens: int,
    prompt: str = "autotest publication",
):
    return IngestClient(client).post_signal(
        "event-hooks",
        hook_round(session_id, repo, round_no, input_tokens, prompt),
        CT_NDJSON,
        project=project,
        extra_headers=headers,
    )


# ── Response checks ─────────────────────────────────────────────────────────────


def echo_of(response) -> dict:
    """The publication echo headers present on a response (empty for legacy and every refusal)."""
    return {
        name: response.headers[name]
        for name in ECHO_HEADERS
        if name in response.headers
    }


def assert_publication_rejected(
    response, status_code: int, reason: str, disposition: str
) -> None:
    """The exact contract refusal: status, `{"reason", "disposition"}` body and no publication echo."""
    assert_that(
        response.status_code,
        equal_to(status_code),
        f"expected HTTP {status_code} {reason}, got: {response.text[:300]}",
    )
    assert_that(
        response.json(),
        equal_to({"reason": reason, "disposition": disposition}),
        "contract refusal body",
    )
    assert_that(
        echo_of(response), empty(), "a refused request carries no publication echo"
    )


def assert_hooks_accepted_without_echo(response, what: str) -> None:
    """A hook batch stored on the legacy or L1 path: HTTP 200 `{}` and no publication echo."""
    assert_that(
        (response.status_code, response.text),
        equal_to((200, "{}")),
        f"{what}: expected the batch accepted",
    )
    assert_that(echo_of(response), empty(), f"{what}: a legacy answer has no echo")


def assert_hooks_discarded(response, reason: str, what: str) -> None:
    """The whole hook batch discarded for `reason` with the existing 200 discard body."""
    assert_that(response.status_code, equal_to(200), f"{what}: {response.text[:300]}")
    assert_that(
        response.json(),
        has_entries({"accepted": 0, "reason": reason}),
        f"{what}: expected the batch discarded for {reason}",
    )
    assert_that(echo_of(response), empty(), f"{what}: a discard has no echo")


# ── Reads ───────────────────────────────────────────────────────────────────────


def read_endpoint(client, endpoint: str, params: dict) -> dict:
    """The raw JSON of a CLI analytics read endpoint (data and metadata); fails on non-200."""
    response = CliAnalyticsApi(client).request(
        "GET", f"{INGEST_PREFIX}/{endpoint}", params=params
    )
    assert_that(
        response.status_code,
        equal_to(200),
        f"{endpoint}: {response.status_code} {response.text[:300]}",
    )
    return response.json()


def session_rows(client, params: dict) -> dict:
    """{trace_id: row} of every session of one /sessions page (per_page 500) for `params`."""
    body = read_endpoint(client, "sessions", {"per_page": 500, **params})
    return {row["trace_id"]: row for row in body["data"]["sessions"]}


def own_personal_rows(client, search: str) -> dict:
    """{trace_id: row} of the caller's own Personal sessions matching `search` (personal analytics)."""
    return session_rows(client, {"personal_only": "true", "search": search})


def wait_for_session_row(
    client, params: dict, session_id: str, input_tokens: int | None = None
) -> dict | None:
    """The /sessions row of `session_id` for `params` once it is listed (with `input_tokens` stored,
    when given), or None when it never was within PRESENCE_TIMEOUT_S."""

    def _ready(row) -> bool:
        return row is not None and (
            input_tokens is None or row["input_tokens"] == input_tokens
        )

    row = poll_until(
        lambda: session_rows(client, params).get(session_id),
        _ready,
        timeout=PRESENCE_TIMEOUT_S,
        interval=PRESENCE_POLL_S,
    )
    return row if _ready(row) else None


def assert_buckets_shape(payload: dict, what: str) -> None:
    """`funding` has exactly the three routes and every bucket (with `personal_billed`) the four fields."""
    assert_that(sorted(payload["funding"]), equal_to(sorted(FUNDING_ROUTES)), what)
    for bucket in [*payload["funding"].values(), payload["personal_billed"]]:
        assert_that(
            sorted(bucket),
            equal_to(sorted(EMPTY_BUCKET)),
            f"{what}: fields of a funding bucket",
        )
        assert_that(
            bucket["cost_incomplete"],
            equal_to(bool(bucket["unpriced_models"])),
            f"{what}: cost_incomplete must say whether the bucket has unpriced models",
        )


def funding_session_total(payload: dict) -> int:
    return sum(bucket["session_count"] for bucket in payload["funding"].values())


def funding_cost_total(payload: dict) -> float:
    return sum(bucket["cost_usd"] for bucket in payload["funding"].values())
