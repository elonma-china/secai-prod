// @vitest-environment jsdom
import { webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { sha256File } from "./fileFingerprint";

afterEach(() => vi.unstubAllGlobals());

describe("file fingerprint in LAN HTTP contexts", () => {
  it.each([
    ["empty", new Uint8Array(), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    ["UTF-8", new TextEncoder().encode("Tiếng Việt 😀"), "bd7b4426e12470860f7738247d3c13ffd9c85ae403acd5c2252b9ac26a3c4dd5"],
    ["multiblock", new TextEncoder().encode("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"],
    ["binary", new Uint8Array([0, 255, 128, 65]), "3507b01e644277ad3cd10dadd6e33cb801151e62e3cb899a67409ef701d6079c"],
  ])("hashes %s identically without crypto.subtle", async (name, bytes, expected) => {
    vi.stubGlobal("crypto", { getRandomValues: webcrypto.getRandomValues.bind(webcrypto), subtle: undefined });
    vi.stubGlobal("isSecureContext", false);
    await expect(sha256File(new File([bytes], `${name}.txt`))).resolves.toBe(expected);
  });

  it("keeps the native digest path equivalent when available", async () => {
    vi.stubGlobal("crypto", webcrypto);
    await expect(sha256File(new File(["abc"], "a.txt"))).resolves
      .toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});
