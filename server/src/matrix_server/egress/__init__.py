"""Single outbound-request policy for user-supplied URLs (ADR-012, contract G0 section 3).

Webhooks, scanner, runner and any future caller of a user URL use only `egress_client(...)`.
Operator-listed local model servers (ADR-016) are reached only through `local_model_client(...)`.
"""

from matrix_server.egress.models import EgressHost  # noqa: I001  (first: storage.models imports this package)
from matrix_server.egress.client import (
    EgressClient,
    EgressResponse,
    egress_client,
    set_dialer_for_tests,
)
from matrix_server.egress.local_models import (
    LOCAL_MODEL_PURPOSES,
    LocalEndpoint,
    LocalModelClient,
    LocalModelSettings,
    check_local_url,
    get_local_model_settings,
    local_llm_available,
    local_model_client,
    parse_local_endpoints,
    resolve_local_model_base_url,
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
    "LOCAL_MODEL_PURPOSES",
    "EgressClient",
    "EgressError",
    "EgressHost",
    "EgressResponse",
    "EgressSettings",
    "EgressTarget",
    "LocalEndpoint",
    "LocalModelClient",
    "LocalModelSettings",
    "check_local_url",
    "check_url",
    "classify_ip",
    "egress_client",
    "get_local_model_settings",
    "host_matches",
    "local_llm_available",
    "local_model_client",
    "normalize_host",
    "parse_local_endpoints",
    "resolve_local_model_base_url",
    "set_dialer_for_tests",
    "set_resolver_for_tests",
    "validate_allowlist_entry",
]
