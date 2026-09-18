package io.watts.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * WATTS control-plane entry point (scaffold).
 *
 * <p>The behaviour this service is built against is specified and tested in the Python
 * reference implementation under {@code services/}. See {@code apps/api/README.md}.
 */
@SpringBootApplication
public class WattsApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(WattsApiApplication.class, args);
    }
}
