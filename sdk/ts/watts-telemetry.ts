/**
 * WATTS LLM telemetry SDK (TypeScript).
 *
 * Same contract as the Python SDK: counts, timings, identifiers and hashes. The API
 * accepts token counts, never message content, so an integration cannot ship prompts.
 */
/**
 * How much identity leaves the process. Mirrors PrivacyMode in the Python SDK.
 *
 * There is no mode that sends prompt or completion text: the record type has no field for
 * it. Privacy here is a property of the schema, not a setting someone can flip.
 */
export type PrivacyMode =
  | "strict"     // counts, timings, per-tenant salted hashes. The default.
  | "metadata"   // plus caller-supplied scalar labels, still validated downstream
  | "no-hashes"; // counts and timings only; disables duplicate and prefix detection

export interface TelemetryConfig {
  workload: string;
  tenant?: string;
  endpoint?: string;
  hashSalt?: string;
  securityPolicy?: string;
  dryRun?: boolean;
  sampleRate?: number;
  /** Defaults to "strict". Raise it deliberately, never as a convenience. */
  privacyMode?: PrivacyMode;
  signer?: (canonicalJson: string) => Promise<string>; // HMAC provided by the host app
}

export interface SpanInit {
  model: string;
  provider: string;
  taskClass?: string;
  gpuId?: string;
  contextWindow?: number;
  sessionId?: string;
  agentStep?: number;
}

export interface TelemetryRecord {
  request_id: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  gpu_id: string;
  tenant: string;
  timestamp: number;
  security_policy: string;
  task_class: string;
  batch_size: number;
  context_window: number;
  success: boolean;
  cache_hit: boolean;
  agent_step: number;
  prompt_hash?: string;
  context_prefix_hash?: string;
  session_id_hash?: string;
  energy_wh?: number;
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function stableHash(value: string, salt = ""): Promise<string> {
  return (await sha256Hex(`${salt}\u001f${value}`)).slice(0, 16);
}

export class WattsTelemetry {
  constructor(private config: TelemetryConfig) {}

  async track<T>(init: SpanInit, fn: (span: Span) => Promise<T>): Promise<T> {
    const span = new Span(init, this.config);
    try {
      return await fn(span);
    } catch (err) {
      span.markFailed();
      throw err;
    } finally {
      await this.emit(span);
    }
  }

  private async emit(span: Span): Promise<void> {
    if (Math.random() > (this.config.sampleRate ?? 1)) return;
    const record = span.toRecord();
    const canonical = JSON.stringify(record, Object.keys(record).sort());
    const signature = this.config.signer ? await this.config.signer(canonical) : "";
    if (this.config.dryRun !== false || !this.config.endpoint) {
      console.debug("[watts:dry-run]", canonical);
      return;
    }
    await fetch(`${this.config.endpoint.replace(/\/$/, "")}/v1/telemetry`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-WATTS-Signature": signature,
        "X-WATTS-Workload": this.config.workload,
      },
      body: JSON.stringify({ workload: this.config.workload, record, signature }),
    });
  }
}

export class Span {
  private start = performance.now();
  private inputTokens = 0;
  private outputTokens = 0;
  private batchSize = 1;
  private success = true;
  private cacheHit = false;
  private promptHash?: string;
  private prefixHash?: string;
  private sessionHash?: string;
  private energyWh?: number;

  constructor(private init: SpanInit, private config: TelemetryConfig) {}

  setTokens(inputTokens: number, outputTokens: number): this {
    this.inputTokens = inputTokens;
    this.outputTokens = outputTokens;
    return this;
  }

  async setPromptIdentity(prompt?: string, contextPrefix?: string): Promise<this> {
    const salt = this.config.hashSalt ?? this.config.tenant ?? "";
    if (prompt !== undefined) this.promptHash = await stableHash(prompt, salt);
    if (contextPrefix !== undefined) this.prefixHash = await stableHash(contextPrefix, salt);
    return this;
  }

  setBatch(size: number): this { this.batchSize = Math.max(1, size); return this; }
  setEnergy(wh: number): this { this.energyWh = wh; return this; }
  markFailed(): this { this.success = false; return this; }
  markCacheHit(): this { this.cacheHit = true; return this; }

  toRecord(): TelemetryRecord {
    return {
      request_id: crypto.randomUUID().slice(0, 18),
      model: this.init.model,
      provider: this.init.provider,
      input_tokens: this.inputTokens,
      output_tokens: this.outputTokens,
      latency_ms: performance.now() - this.start,
      gpu_id: this.init.gpuId ?? "unknown",
      tenant: this.config.tenant ?? "default",
      timestamp: Date.now() / 1000,
      security_policy: this.config.securityPolicy ?? "default",
      task_class: this.init.taskClass ?? "unspecified",
      batch_size: this.batchSize,
      context_window: this.init.contextWindow ?? 0,
      success: this.success,
      cache_hit: this.cacheHit,
      agent_step: this.init.agentStep ?? 0,
      prompt_hash: this.promptHash,
      context_prefix_hash: this.prefixHash,
      session_id_hash: this.sessionHash,
      energy_wh: this.energyWh,
    };
  }
}
