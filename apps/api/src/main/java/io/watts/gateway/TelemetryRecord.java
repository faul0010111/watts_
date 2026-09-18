package io.watts.gateway;

import jakarta.validation.constraints.*;

/**
 * One LLM request as the energy control plane sees it.
 *
 * <p>There is no field for prompt or completion text, and there never will be. Content
 * identity travels as a salted, truncated SHA-256 hash: enough to say "this exact context
 * was sent 412 times", not enough to reconstruct a single word of it.
 *
 * <p>Mirrors {@code services/telemetry/schema.py}. Change both in the same commit.
 */
public record TelemetryRecord(
        @NotBlank String requestId,
        @NotBlank String model,
        @NotBlank String provider,
        @PositiveOrZero int inputTokens,
        @PositiveOrZero int outputTokens,
        @PositiveOrZero double latencyMs,
        @NotBlank String gpuId,
        @NotBlank String tenant,
        double timestamp,
        String securityPolicy,
        @Size(max = 32) String promptHash,
        @Size(max = 32) String contextPrefixHash,
        @Size(max = 32) String sessionIdHash,
        String taskClass,
        @Positive int batchSize,
        @PositiveOrZero int contextWindow,
        boolean success,
        boolean cacheHit,
        @PositiveOrZero int agentStep,
        Double energyWh,
        String energyProvenance) {

    public int totalTokens() {
        return inputTokens + outputTokens;
    }

    /** Canonical serialisation used for signing. Field order is part of the contract. */
    public String canonicalJson() {
        return CanonicalJson.of(this);
    }
}
