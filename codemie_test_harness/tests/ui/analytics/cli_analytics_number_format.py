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

import math
from decimal import ROUND_HALF_UP, Decimal


def _locale(n: float, decimals: int | None = None) -> str:
    """``toLocaleString('en-US')`` for integers, or with fixed fraction digits."""
    if decimals is None:
        return f"{int(n):,}"
    q = Decimal(str(n)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{q:,}"


def _currency(v: float) -> str:
    """``formatCurrency``: two decimals, but a positive amount below a cent keeps two
    significant digits; the sign follows the ``$``."""
    if 0 < v < 0.01:
        exact = Decimal(str(v))
        q = exact.quantize(
            Decimal(1).scaleb(exact.adjusted() - 1), rounding=ROUND_HALF_UP
        )
        return f"${q.normalize():f}"
    sign = "-" if math.copysign(1, v) < 0 else ""
    return f"${sign}{_locale(abs(v), 2)}"
