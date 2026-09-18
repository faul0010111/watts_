# Guardrails for the WATTS assistant itself.
#
# The optimisation LLM is a principal with deliberately weak rights: it may read
# telemetry, run simulations and propose changes. It may not approve, execute, read
# prompts, or reach another tenant's data. Prompt injection inside telemetry therefore
# cannot escalate into an infrastructure change - the worst it can produce is a proposal
# that a human reviews.

package watts.llm

import rego.v1

default allow := false

allowed_tools := {"query_telemetry", "run_simulation", "propose_recommendation", "explain_policy"}

allow if {
	input.principal_type == "assistant"
	input.tool in allowed_tools
	count(deny) == 0
}

deny contains msg if {
	input.principal_type == "assistant"
	input.tool in {"execute_change", "approve_optimization", "update_policy", "rotate_key"}
	msg := sprintf("WATTS-LLM-001: assistant may not call %q", [input.tool])
}

deny contains msg if {
	input.principal_type == "assistant"
	input.requested_tenant != input.session_tenant
	msg := "WATTS-LLM-002: assistant may not read across tenants"
}

deny contains msg if {
	input.principal_type == "assistant"
	input.requests_raw_content
	msg := "WATTS-LLM-003: prompt and completion text are not available to any principal"
}

deny contains msg if {
	input.principal_type == "assistant"
	input.tool_calls_in_session > 50
	msg := "WATTS-LLM-004: tool-call budget exhausted for this session"
}
