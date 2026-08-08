import { createPublicKey } from "node:crypto";

import { expect, test } from "vitest";

import { PUBLIC_KEY_PEM } from "../src/public-key";

test("published key material is a prime256v1 EC public key", () => {
  const key = createPublicKey(PUBLIC_KEY_PEM);

  expect(key.asymmetricKeyType).toBe("ec");
  expect(key.asymmetricKeyDetails?.namedCurve).toBe("prime256v1");
});
