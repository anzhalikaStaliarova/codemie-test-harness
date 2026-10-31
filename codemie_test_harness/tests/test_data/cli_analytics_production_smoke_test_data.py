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

"""Expected metadata of the CLI session seeded by the Production release smoke run.

Both the Run ID and its expected metadata come from the environment: the suite never
hardcodes a Production host, user, or Run ID value, and reads them only when both
environment variables below are set, so it stays safe to collect in every other CI run.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

SMOKE_RUN_ID_ENV = "CLI_ANALYTICS_SMOKE_RUN_ID"
SMOKE_SESSION_FIXTURE_ENV = "CLI_ANALYTICS_SMOKE_SESSION_FIXTURE"

_REQUIRED_KEYS = (
    "model",
    "user",
    "repository",
    "branch",
    "project",
    "agent",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "duration_ms",
)


@dataclass(frozen=True)
class ProductionSmokeSession:
    """Known-good metadata of the Production smoke-test CLI session."""

    run_id: str
    model: str
    user: str
    repository: str
    branch: str
    project: str
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def load_production_smoke_session() -> ProductionSmokeSession:
    """Build the expected session metadata from the environment, or skip the test.

    Skips (does not fail) when either environment variable is absent or the fixture file
    does not exist, so these Production-only checks are safe to collect anywhere else.
    """
    run_id = (os.environ.get(SMOKE_RUN_ID_ENV) or "").strip()
    if not run_id:
        pytest.skip(
            f"{SMOKE_RUN_ID_ENV} is not set; skipping the Production smoke session checks"
        )
    fixture_path = (os.environ.get(SMOKE_SESSION_FIXTURE_ENV) or "").strip()
    if not fixture_path:
        pytest.skip(
            f"{SMOKE_SESSION_FIXTURE_ENV} is not set; skipping the Production smoke "
            "session checks"
        )
    path = Path(fixture_path)
    if not path.is_file():
        pytest.skip(
            f"{SMOKE_SESSION_FIXTURE_ENV}={fixture_path!r} does not point to an existing "
            "file; skipping the Production smoke session checks"
        )
    metadata = json.loads(path.read_text())
    missing = [key for key in _REQUIRED_KEYS if key not in metadata]
    if missing:
        pytest.fail(
            f"Production smoke session fixture {fixture_path!r} is missing keys: {missing}"
        )
    return ProductionSmokeSession(
        run_id=run_id, **{k: metadata[k] for k in _REQUIRED_KEYS}
    )
