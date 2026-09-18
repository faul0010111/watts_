package io.watts.gateway;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;

/**
 * The trust boundary of WATTS.
 *
 * <p>Everything downstream treats telemetry as ground truth, so authenticity and
 * plausibility are established once, here. A rejected record is counted and surfaced,
 * never silently dropped: a gap in telemetry is itself a signal.
 *
 * <p>Mirrors {@code services/telemetry/gateway.py}.
 */
@RestController
@RequestMapping("/v1/telemetry")
public class TelemetryController {

    private final TelemetryVerifier verifier;
    private final TelemetryPublisher publisher;

    public TelemetryController(TelemetryVerifier verifier, TelemetryPublisher publisher) {
        this.verifier = verifier;
        this.publisher = publisher;
    }

    @PostMapping
    public Mono<ResponseEntity<IngestResponse>> ingest(
            @RequestHeader("X-WATTS-Workload") String workload,
            @RequestHeader("X-WATTS-Signature") String signature,
            @Valid @RequestBody TelemetryRecord record) {

        return verifier.verify(workload, signature, record)
                .flatMap(verdict -> {
                    if (!verdict.accepted()) {
                        // 422, not 401: the credential may be fine and the physics wrong.
                        return Mono.just(ResponseEntity
                                .status(HttpStatus.UNPROCESSABLE_ENTITY)
                                .body(new IngestResponse(false, verdict.reason(), verdict.detail())));
                    }
                    return publisher.publish(workload, record)
                            .thenReturn(ResponseEntity.accepted()
                                    .body(new IngestResponse(true, null, null)));
                });
    }

    public record IngestResponse(boolean accepted, String reason, String detail) {}
}
