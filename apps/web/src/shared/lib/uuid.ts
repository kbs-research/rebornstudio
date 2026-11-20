/**
 * 무작위 UUID(v4) 생성.
 *
 * `crypto.randomUUID`는 보안 컨텍스트(https·localhost)에서만 있다.
 * 평문 http 주소로 연 화면에서는 `undefined`라서 부르면 TypeError가 난다.
 * `crypto.getRandomValues`는 보안 컨텍스트가 아니어도 있으므로 그 값으로 같은 형식을 만든다.
 */

/**
 * UUID 모양의 문자열이면 true. 주소에서 받은 id를 서버에 보내기 전에 거른다.
 * 모양이 틀린 id를 보내면 서버가 422(입력값 오류)를 돌려주어, 화면이 "없는 대상"과 다른 안내를 띄운다.
 */
export function isUuid(s: string | null | undefined): boolean {
  return !!s && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
}

/** 무작위 UUID(v4) 문자열을 만든다. `crypto.randomUUID`가 없으면 `getRandomValues`로 만든다. */
export function randomUuid(): string {
  const c = globalThis.crypto;
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  // 버전 4와 변형 10을 표시해 `randomUUID`와 같은 형식으로 맞춘다(RFC 9562 4.1·4.2).
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
