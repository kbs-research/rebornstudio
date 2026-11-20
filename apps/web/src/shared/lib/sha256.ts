/**
 * SHA-256 해시.
 *
 * `crypto.subtle`은 보안 컨텍스트(https·localhost)에서만 있다.
 * 평문 http 주소로 연 화면에서는 `undefined`라서 업로드의 내용 해시를 만들지 못한다.
 * 그때는 같은 알고리즘의 순수 스크립트 구현으로 계산한다(FIPS 180-4).
 * 서버가 이 값으로 같은 프로젝트의 중복 업로드를 찾으므로 두 경로의 결과가 같아야 한다.
 */

/** 라운드 상수. 처음 64개 소수의 세제곱근 소수부 32비트다. */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/** 초기 해시값. 처음 8개 소수의 제곱근 소수부 32비트다. */
const H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

/** 64바이트 블록 하나를 압축해 `h`를 갱신한다. `w`는 호출마다 덮어쓰는 작업 공간이다. */
function compress(h: Uint32Array, w: Uint32Array, view: DataView, off: number): void {
  for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
  for (let i = 16; i < 64; i++) {
    const a = w[i - 15];
    const b = w[i - 2];
    const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
    const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
    w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
  }
  let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
  for (let i = 0; i < 64; i++) {
    const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
    const ch = (e & f) ^ (~e & g);
    const t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
    const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
    const maj = (a & b) ^ (a & c) ^ (b & c);
    const t2 = (S0 + maj) | 0;
    hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
  }
  // `Uint32Array`에 넣으면 2^32로 나눈 나머지가 남는다.
  h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

/** 바이트 배열의 SHA-256을 스크립트로 계산한다. `crypto.subtle`이 없을 때 쓰는 경로다. */
export function sha256Sync(data: Uint8Array): Uint8Array {
  const h = new Uint32Array(H0);
  const w = new Uint32Array(64);
  const len = data.byteLength;
  const full = len - (len % 64);
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  for (let off = 0; off < full; off += 64) compress(h, w, view, off);

  // 남은 바이트 뒤에 0x80과 0을 채우고, 마지막 8바이트에 비트 길이를 빅엔디언으로 쓴다.
  const rem = len - full;
  const tail = new Uint8Array(rem < 56 ? 64 : 128);
  tail.set(data.subarray(full));
  tail[rem] = 0x80;
  const tv = new DataView(tail.buffer);
  tv.setUint32(tail.length - 8, Math.floor(len / 0x20000000));
  tv.setUint32(tail.length - 4, (len * 8) >>> 0);
  for (let off = 0; off < tail.length; off += 64) compress(h, w, tv, off);

  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i]);
  return out;
}

/**
 * SHA-256을 계산한다. `crypto.subtle`이 있으면 `crypto.subtle`로, 없으면 `sha256Sync`로 계산한다.
 *
 * 스크립트 경로는 메인 스레드를 막는다. 업로드 파트 하나(16 MiB)에 수십 밀리초가 걸린다.
 */
export async function sha256(data: ArrayBuffer | Uint8Array<ArrayBuffer>): Promise<Uint8Array> {
  const subtle = globalThis.crypto?.subtle;
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (subtle) return new Uint8Array(await subtle.digest("SHA-256", bytes));
  return sha256Sync(bytes);
}
