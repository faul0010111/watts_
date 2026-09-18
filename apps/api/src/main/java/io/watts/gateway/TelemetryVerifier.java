package io.watts.gateway;

import reactor.core.publisher.Mono;

/**
 * Authenticity, freshness and physics.
 *
 * <p>Implementations must check, in this order:
 * <ol>
 *   <li>the workload is known and its HMAC key resolves;</li>
 *   <li>the signature matches, compared in constant time;</li>
 *   <li>the timestamp is within the permitted clock skew;</li>
 *   <li>the request id has not been seen inside the replay window;</li>
 *   <li>the numbers are physically possible — energy implying a device power no accelerator
 *       can draw, watt-hours per thousand tokens outside plausible bounds, or an input
 *       larger than the declared context window.</li>
 * </ol>
 *
 * <p>Step five exists because a workload that under-reports energy would otherwise top the
 * efficiency leaderboard, and one that over-reports could make a rival tenant look
 * expensive. See {@code docs/THREAT_MODEL.md}, T1 and T2.
 */
public interface TelemetryVerifier {

    Mono<Verdict> verify(String workload, String signature, TelemetryRecord record);

    record Verdict(boolean accepted, String reason, String detail) {
        public static Verdict ok() {
            return new Verdict(true, null, null);
        }

        public static Verdict reject(String reason, String detail) {
            return new Verdict(false, reason, detail);
        }
    }
}
