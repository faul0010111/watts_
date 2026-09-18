package io.watts.gateway;

import java.util.Locale;
import java.util.StringJoiner;

/**
 * Deterministic serialisation for signing.
 *
 * <p>Keys are sorted and formatting is fixed, so the same record produces the same bytes in
 * the SDK, the gateway and the Python reference implementation. Any divergence here breaks
 * every signature, which is why the field list is explicit rather than reflective.
 */
final class CanonicalJson {

    private CanonicalJson() {}

    static String of(TelemetryRecord r) {
        StringJoiner j = new StringJoiner(",", "{", "}");
        j.add(kv("agent_step", r.agentStep()));
        j.add(kv("batch_size", r.batchSize()));
        j.add(kv("cache_hit", r.cacheHit()));
        j.add(kv("context_prefix_hash", r.contextPrefixHash()));
        j.add(kv("context_window", r.contextWindow()));
        j.add(kv("energy_provenance", r.energyProvenance()));
        j.add(kv("energy_wh", r.energyWh()));
        j.add(kv("gpu_id", r.gpuId()));
        j.add(kv("input_tokens", r.inputTokens()));
        j.add(kv("latency_ms", r.latencyMs()));
        j.add(kv("model", r.model()));
        j.add(kv("output_tokens", r.outputTokens()));
        j.add(kv("prompt_hash", r.promptHash()));
        j.add(kv("provider", r.provider()));
        j.add(kv("request_id", r.requestId()));
        j.add(kv("security_policy", r.securityPolicy()));
        j.add(kv("session_id_hash", r.sessionIdHash()));
        j.add(kv("success", r.success()));
        j.add(kv("task_class", r.taskClass()));
        j.add(kv("tenant", r.tenant()));
        j.add(kv("timestamp", r.timestamp()));
        return j.toString();
    }

    private static String kv(String key, Object value) {
        return "\"" + key + "\":" + literal(value);
    }

    private static String literal(Object value) {
        if (value == null) return "null";
        if (value instanceof String s) return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
        if (value instanceof Boolean b) return b ? "true" : "false";
        if (value instanceof Double d) return String.format(Locale.ROOT, "%s", d);
        return String.valueOf(value);
    }
}
