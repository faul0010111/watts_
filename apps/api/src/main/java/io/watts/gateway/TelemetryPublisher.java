package io.watts.gateway;

import reactor.core.publisher.Mono;

/**
 * Hands a verified record to the stream.
 *
 * <p>Partition by tenant so a single tenant's traffic cannot reorder another's, and so
 * tenant-scoped consumers stay tenant-scoped. Nothing here may write prompt content,
 * because the record has none to write.
 */
public interface TelemetryPublisher {
    Mono<Void> publish(String workload, TelemetryRecord record);
}
