package io.watts.parity;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.stream.Stream;
import java.util.stream.StreamSupport;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * The JVM control plane must reproduce the Python reference implementation exactly.
 *
 * <p>Both read {@code parity/vectors.json}: fixed inputs with the outputs the reference
 * produces. Two implementations of the same rules drift unless something forces them not
 * to, and a simulator that allows what production denies is the worst failure this project
 * could have.
 *
 * <p>When a case fails, the fix is to change the Java implementation. Regenerating the
 * vectors to make a failing implementation pass defeats the entire purpose; regenerate only
 * when the reference behaviour changed deliberately, in the same commit as that change.
 */
class ParityVectorsTest {

    private static final Path VECTORS = Path.of("..", "..", "parity", "vectors.json");
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static JsonNode vectors;
    private static double tolerance;

    @BeforeAll
    static void load() throws Exception {
        assertTrue(Files.exists(VECTORS),
                "parity/vectors.json is missing; run `python tools/generate_parity_vectors.py`");
        vectors = MAPPER.readTree(Files.readString(VECTORS));
        tolerance = vectors.get("tolerance").asDouble();
    }

    static Stream<JsonNode> attributionCases() throws Exception {
        return cases("attribution");
    }

    static Stream<JsonNode> policyCases() throws Exception {
        return cases("policy");
    }

    static Stream<JsonNode> budgetCases() throws Exception {
        return cases("budgets");
    }

    static Stream<JsonNode> canonicalJsonCases() throws Exception {
        return cases("canonical_json");
    }

    private static Stream<JsonNode> cases(String section) throws Exception {
        if (vectors == null) {
            load();
        }
        return StreamSupport.stream(vectors.get(section).spliterator(), false);
    }

    /**
     * Energy attributed to input and output tokens.
     *
     * <p>A routing decision built on a different prefill/decode split is a different
     * decision, so the two implementations must agree to within {@code tolerance}.
     */
    @ParameterizedTest(name = "attribution {0}")
    @MethodSource("attributionCases")
    void attributionMatchesReference(JsonNode testCase) {
        JsonNode in = testCase.get("input");
        JsonNode expected = testCase.get("expected");
        // var actual = attributionService.attribute(
        //         in.get("energy_wh").asDouble(), in.get("input_tokens").asInt(),
        //         in.get("output_tokens").asInt(), profiles.get(in.get("model").asText()));
        // assertEquals(expected.get("input_energy_wh").asDouble(), actual.inputEnergyWh(), tolerance);
        // assertEquals(expected.get("output_energy_wh").asDouble(), actual.outputEnergyWh(), tolerance);
        fail("attribution service not implemented yet: " + testCase.get("id").asText()
                + " (expected input share " + expected.get("input_energy_wh").asDouble() + " Wh)");
    }

    /**
     * Policy decisions, including which rule fired.
     *
     * <p>Agreeing on allow/deny is not enough: the rule id is what an operator reads in the
     * audit trail, and two systems citing different rules for the same denial is a
     * compliance problem of its own.
     */
    @ParameterizedTest(name = "policy {0}")
    @MethodSource("policyCases")
    void policyDecisionsMatchReference(JsonNode testCase) {
        JsonNode expected = testCase.get("expected");
        // var decision = policyEngine.evaluate(PolicyInput.fromJson(testCase.get("input")));
        // assertEquals(expected.get("allow").asBoolean(), decision.allow());
        // assertEquals(expected.get("rule_id").asText(), decision.ruleId());
        fail("policy engine not implemented yet: " + testCase.get("id").asText()
                + " (expected " + expected.get("rule_id").asText() + ")");
    }

    /** Budget projection and utilisation, which drive chargeback. */
    @ParameterizedTest(name = "budget {0}")
    @MethodSource("budgetCases")
    void budgetCalculationsMatchReference(JsonNode testCase) {
        fail("budget tracker not implemented yet: " + testCase.get("id").asText());
    }

    /**
     * Canonical serialisation and its HMAC.
     *
     * <p>If the SDK, the gateway and the reference serialise a record differently, every
     * signature fails and the trust boundary stops working. This is the cheapest of the
     * four to get wrong and the most expensive to debug in production.
     */
    @ParameterizedTest(name = "canonical json {0}")
    @MethodSource("canonicalJsonCases")
    void canonicalJsonMatchesReference(JsonNode testCase) {
        JsonNode expected = testCase.get("expected");
        // TelemetryRecord record = MAPPER.treeToValue(testCase.get("input"), TelemetryRecord.class);
        // assertEquals(expected.get("canonical_json").asText(), record.canonicalJson());
        // assertEquals(expected.get("signature_hex").asText(),
        //         Hmac.sha256Hex(expected.get("hmac_key_utf8").asText(), record.canonicalJson()));
        fail("canonical JSON not wired to TelemetryRecord yet: "
                + testCase.get("id").asText());
    }
}
