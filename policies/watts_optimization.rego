# WATTS optimisation policy.
#
# The rule that defines the project: energy is never a reason to weaken a control, delay
# critical work, or spend latency the SLO does not have.

package watts.optimization

import rego.v1

default allow := false

allow if {
	count(deny) == 0
	input.action in {"apply_optimization", "execute_change"}
}

# WATTS-SEC-003: an optimisation that weakens a security control is blocked outright.
deny contains msg if {
	input.action in {"apply_optimization", "execute_change"}
	input.weakens_control
	msg := sprintf("WATTS-SEC-003: optimisation weakens control %q; energy savings are not a valid trade", [object.get(input, "control_touched", "unspecified")])
}

# WATTS-SEC-004: infrastructure changes require a recorded human approval.
deny contains msg if {
	input.action == "execute_change"
	input.requires_human_approval
	not input.human_approved
	msg := "WATTS-SEC-004: human approval required before execution"
}

# WATTS-SEC-006: critical workloads are never deferred, downgraded or throttled for energy.
deny contains msg if {
	input.action == "apply_optimization"
	input.workload_criticality == "critical"
	input.optimization_type in {"defer_workload", "downgrade_model", "throttle"}
	msg := sprintf("WATTS-SEC-006: %q is not available for critical workloads", [input.optimization_type])
}

# WATTS-SEC-007: an optimisation may not consume more latency than the SLO has left.
deny contains msg if {
	input.action == "apply_optimization"
	input.expected_latency_delta_ms > input.slo_headroom_ms
	msg := sprintf("WATTS-SEC-007: needs %v ms but only %v ms of SLO headroom remain", [input.expected_latency_delta_ms, input.slo_headroom_ms])
}

# WATTS-SEC-008: only operators and SREs execute changes.
deny contains msg if {
	input.action == "execute_change"
	not authorised_role
	msg := "WATTS-SEC-008: principal lacks a role authorised to execute changes"
}

authorised_role if {
	some r in input.roles
	r in {"sre", "operator", "platform-admin"}
}

obligations contains "write_audit_entry" if {
	input.action == "execute_change"
}

obligations contains "record_approval" if {
	input.action == "execute_change"
}
