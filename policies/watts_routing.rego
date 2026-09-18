# WATTS routing policy.
#
# Default deny. A production workload is routed only when the target model and provider
# are explicitly allowed for its data classification.
#
# Mirrored in services/security/policy.py; tests/test_policy_parity.py fails the build
# if the two implementations disagree.

package watts.routing

import rego.v1

default allow := false

allow if {
	count(deny) == 0
	input.action == "route_workload"
}

# WATTS-SEC-001: production traffic only reaches allowlisted models.
deny contains msg if {
	input.action == "route_workload"
	input.environment == "production"
	count(input.approved_models) > 0
	not model_approved
	msg := sprintf("WATTS-SEC-001: model %q is not on the production allowlist", [input.model])
}

model_approved if {
	some m in input.approved_models
	m == input.model
}

provider_approved if {
	some p in input.approved_providers
	p == input.provider
}

# WATTS-SEC-002: sensitive data stays on approved models and providers.
deny contains msg if {
	input.action == "route_workload"
	input.data_classification in {"confidential", "restricted"}
	count(input.approved_providers) > 0
	not provider_approved
	msg := sprintf("WATTS-SEC-002: provider %q not approved for %s data", [input.provider, input.data_classification])
}

deny contains msg if {
	input.action == "route_workload"
	input.data_classification in {"confidential", "restricted"}
	count(input.approved_models) > 0
	not model_approved
	msg := sprintf("WATTS-SEC-002: model %q not approved for %s data", [input.model, input.data_classification])
}

# WATTS-SEC-005: telemetry and routing are tenant-isolated.
deny contains msg if {
	input.action in {"route_workload", "read_telemetry"}
	input.cross_tenant
	msg := "WATTS-SEC-005: cross-tenant access denied"
}
