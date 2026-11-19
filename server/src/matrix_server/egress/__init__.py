"""Single outbound-request policy for user-supplied URLs (ADR-012, contract G0 section 3).

Webhooks, scanner, runner and any future caller of a user URL use only `egress_client(...)`.
"""

from matrix_server.egress.models import EgressHost  # noqa: I001  (first: storage.models imports this package)
from matrix_server.egress.client import (
    EgressClient,
    EgressResponse,
    egress_client,
    set_dialer_for_tests,
)
from matrix_server.egress.policy import (
    EgressError,
    EgressSettings,
    EgressTarget,
    check_url,
    classify_ip,
    host_matches,
    normalize_host,
    set_resolver_for_tests,
    validate_allowlist_entry,
)

__all__ = [
    "EgressClient",
    "EgressError",
    "EgressHost",
    "EgressResponse",
    "EgressSettings",
    "EgressTarget",
    "check_url",
    "classify_ip",
    "egress_client",
    "host_matches",
    "normalize_host",
    "set_dialer_for_tests",
    "set_resolver_for_tests",
    "validate_allowlist_entry",
]
