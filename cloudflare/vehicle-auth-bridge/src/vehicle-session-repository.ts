import type { BeginAuthorizationInput } from "./contracts";
import { base64UrlToBytes, bytesToBase64Url } from "./security";
import type {
  CachedVehicleSnapshot,
  StoredAuthorization,
  VehicleSessionRepository,
} from "./vehicle-session-core";

type ProtectedEnvelope = {
  key_version: 1;
  generation: number;
  iv: string;
  ciphertext: string;
};

export class SqliteVehicleSessionRepository
  implements VehicleSessionRepository
{
  readonly #storage: DurableObjectStorage;
  readonly #keyBytes: Uint8Array;
  readonly #ownerId: string;
  #keyPromise: Promise<CryptoKey> | null = null;

  constructor(
    storage: DurableObjectStorage,
    encryptionKeyBase64Url: string,
    ownerId = "owner-v1",
  ) {
    this.#storage = storage;
    this.#keyBytes = base64UrlToBytes(encryptionKeyBase64Url);
    if (this.#keyBytes.byteLength !== 32) {
      throw new Error("invalid_token_encryption_key");
    }
    this.#ownerId = ownerId;
  }

  async createOAuthLaunch(hash: string, expiresAt: number): Promise<void> {
    this.#storage.sql.exec(
      "DELETE FROM oauth_launches WHERE expires_at < ?",
      Date.now(),
    );
    this.#storage.sql.exec(
      "INSERT OR REPLACE INTO oauth_launches (hash, expires_at) VALUES (?, ?)",
      hash,
      expiresAt,
    );
  }

  async beginAuthorization(input: BeginAuthorizationInput): Promise<boolean> {
    const consumed = this.#storage.sql
      .exec<{ hash: string }>(
        "DELETE FROM oauth_launches WHERE hash = ? AND expires_at >= ? RETURNING hash",
        input.launch_hash,
        input.now_ms,
      )
      .toArray();
    if (consumed.length !== 1) {
      return false;
    }
    this.#storage.sql.exec(
      "DELETE FROM oauth_sessions WHERE expires_at < ?",
      input.now_ms,
    );
    this.#storage.sql.exec(
      "INSERT OR REPLACE INTO oauth_sessions (state_hash, browser_hash, expires_at) VALUES (?, ?, ?)",
      input.state_hash,
      input.browser_hash,
      input.expires_at,
    );
    return true;
  }

  async consumeAuthorization(
    stateHash: string,
    browserHash: string,
    nowMs: number,
  ): Promise<boolean> {
    const consumed = this.#storage.sql
      .exec<{ state_hash: string }>(
        "DELETE FROM oauth_sessions WHERE state_hash = ? AND browser_hash = ? AND expires_at >= ? RETURNING state_hash",
        stateHash,
        browserHash,
        nowMs,
      )
      .toArray();
    return consumed.length === 1;
  }

  async getAuthorization(): Promise<StoredAuthorization | null> {
    return this.#getProtected<StoredAuthorization>("authorization");
  }

  async putAuthorization(value: StoredAuthorization): Promise<void> {
    await this.#putProtected("authorization", value.generation, value);
  }

  async replaceAuthorizationAndClearSnapshot(
    value: StoredAuthorization,
  ): Promise<void> {
    const protectedValue = await this.#protectValue(
      "authorization",
      value.generation,
      value,
    );
    const updatedAt = Date.now();
    this.#storage.transactionSync(() => {
      this.#writeProtected("authorization", protectedValue, updatedAt);
      this.#storage.sql.exec(
        "DELETE FROM protected_state WHERE key = ?",
        "vehicle_summary",
      );
    });
  }

  async markReauthorizationRequired(): Promise<void> {
    const authorization = await this.getAuthorization();
    if (!authorization) {
      return;
    }
    await this.putAuthorization({
      ...authorization,
      reauthorization_required: true,
    });
  }

  async getCachedSnapshot(): Promise<CachedVehicleSnapshot | null> {
    return this.#getProtected<CachedVehicleSnapshot>("vehicle_summary");
  }

  async putCachedSnapshot(value: CachedVehicleSnapshot): Promise<void> {
    await this.#putProtected(
      "vehicle_summary",
      value.snapshot.checked_at_ms,
      value,
    );
  }

  async clearCachedSnapshot(): Promise<void> {
    this.#storage.sql.exec(
      "DELETE FROM protected_state WHERE key = ?",
      "vehicle_summary",
    );
  }

  async #putProtected(
    recordKey: string,
    generation: number,
    value: unknown,
  ): Promise<void> {
    const protectedValue = await this.#protectValue(
      recordKey,
      generation,
      value,
    );
    this.#writeProtected(recordKey, protectedValue, Date.now());
  }

  async #protectValue(
    recordKey: string,
    generation: number,
    value: unknown,
  ): Promise<string> {
    const key = await this.#cryptoKey();
    const iv = new Uint8Array(12);
    crypto.getRandomValues(iv);
    const additionalData = this.#additionalData(recordKey, generation);
    const plaintext = new TextEncoder().encode(JSON.stringify(value));
    const encrypted = await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv,
        additionalData,
      },
      key,
      plaintext,
    );
    const envelope: ProtectedEnvelope = {
      key_version: 1,
      generation,
      iv: bytesToBase64Url(iv),
      ciphertext: bytesToBase64Url(new Uint8Array(encrypted)),
    };
    return JSON.stringify(envelope);
  }

  #writeProtected(
    recordKey: string,
    protectedValue: string,
    updatedAt: number,
  ): void {
    this.#storage.sql.exec(
      "INSERT OR REPLACE INTO protected_state (key, value, updated_at) VALUES (?, ?, ?)",
      recordKey,
      protectedValue,
      updatedAt,
    );
  }

  async #getProtected<T>(recordKey: string): Promise<T | null> {
    const rows = this.#storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM protected_state WHERE key = ?",
        recordKey,
      )
      .toArray();
    if (rows.length === 0) {
      return null;
    }
    let envelope: ProtectedEnvelope;
    try {
      envelope = JSON.parse(rows[0].value) as ProtectedEnvelope;
      if (
        envelope.key_version !== 1 ||
        !Number.isSafeInteger(envelope.generation)
      ) {
        throw new Error("invalid_envelope");
      }
      const iv = base64UrlToBytes(envelope.iv);
      const ciphertext = base64UrlToBytes(envelope.ciphertext);
      if (iv.byteLength !== 12 || ciphertext.byteLength < 16) {
        throw new Error("invalid_envelope");
      }
      const decrypted = await crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv,
          additionalData: this.#additionalData(
            recordKey,
            envelope.generation,
          ),
        },
        await this.#cryptoKey(),
        ciphertext,
      );
      return JSON.parse(new TextDecoder().decode(decrypted)) as T;
    } catch {
      throw new Error("protected_state_decrypt_failed");
    }
  }

  #additionalData(recordKey: string, generation: number): Uint8Array {
    return new TextEncoder().encode(
      `${this.#ownerId}|${recordKey}|${generation}|v1`,
    );
  }

  #cryptoKey(): Promise<CryptoKey> {
    this.#keyPromise ??= crypto.subtle.importKey(
      "raw",
      this.#keyBytes,
      { name: "AES-GCM" },
      false,
      ["encrypt", "decrypt"],
    );
    return this.#keyPromise;
  }
}

export function migrateVehicleAuthStorage(
  storage: DurableObjectStorage,
): void {
  storage.sql.exec(`
    CREATE TABLE IF NOT EXISTS _sql_schema_migrations (
      id INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `);
  const version = storage.sql
    .exec<{ version: number }>(
      "SELECT COALESCE(MAX(id), 0) AS version FROM _sql_schema_migrations",
    )
    .one().version;
  if (version < 1) {
    storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS oauth_launches (
        hash TEXT PRIMARY KEY,
        expires_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS oauth_sessions (
        state_hash TEXT PRIMARY KEY,
        browser_hash TEXT NOT NULL,
        expires_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS protected_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_oauth_launches_expiry
        ON oauth_launches(expires_at);
      CREATE INDEX IF NOT EXISTS idx_oauth_sessions_expiry
        ON oauth_sessions(expires_at);
      INSERT INTO _sql_schema_migrations (id) VALUES (1);
    `);
  }
}
