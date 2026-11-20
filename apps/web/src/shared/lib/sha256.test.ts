/**
 * SHA-256 테스트. 평문 http 주소에서 만든 업로드 내용 해시가 https에서 만든 값과 같아야
 * 서버의 중복 감지가 두 주소에서 올린 같은 파일을 같은 파일로 본다.
 */
import { createHash, webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sha256, sha256Sync } from "./sha256";

afterEach(() => vi.unstubAllGlobals());

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const nodeHex = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");
const enc = (s: string) => new TextEncoder().encode(s);

describe("sha256Sync", () => {
  it("FIPS 180-4 예시 값과 같다", () => {
    expect(hex(sha256Sync(enc("")))).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(hex(sha256Sync(enc("abc")))).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(hex(sha256Sync(enc("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"))))
      .toBe("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  });
  it("패딩 경계(55·56·63·64·65바이트 등) 전후의 길이에서 Node 구현과 같다", () => {
    const buf = webcrypto.getRandomValues(new Uint8Array(300));
    for (let n = 0; n <= 300; n++) expect(hex(sha256Sync(buf.subarray(0, n)))).toBe(nodeHex(buf.subarray(0, n)));
  });
  it("버퍼 중간을 가리키는 배열도 그 범위만 해시한다", () => {
    const buf = webcrypto.getRandomValues(new Uint8Array(1000));
    const view = buf.subarray(37, 937);
    expect(hex(sha256Sync(view))).toBe(nodeHex(view));
  });
  it("업로드 파트 크기(16 MiB)에서도 Node 구현과 같다", () => {
    const part = new Uint8Array(16 * 1024 * 1024);
    for (let i = 0; i < part.length; i += 65536) webcrypto.getRandomValues(part.subarray(i, i + 65536));
    expect(hex(sha256Sync(part))).toBe(nodeHex(part));
  });
});

describe("sha256", () => {
  it("crypto.subtle이 없는 주소에서도 subtle과 같은 값을 돌려준다", async () => {
    const data = webcrypto.getRandomValues(new Uint8Array(4097));
    const viaSubtle = hex(await sha256(data));
    vi.stubGlobal("crypto", { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) });
    expect(hex(await sha256(data.buffer))).toBe(viaSubtle);
    expect(viaSubtle).toBe(nodeHex(data));
  });
});
