/**
 * 비밀번호 재설정 입력란에 붙여 넣은 값에서 토큰을 꺼낸다.
 *
 * 안내는 받은 링크를 붙여 넣으라고 하므로 입력란은 링크 전체를 받는다. 링크면 `token` 값을 꺼내고,
 * 토큰만 붙여 넣었으면 그대로 쓴다. 앞뒤 공백과 줄바꿈은 뗀다(메신저에서 복사하면 붙는다).
 * 링크 전체를 그대로 보내면 서버가 400을 내고, 화면은 링크가 만료됐다는 틀린 원인을 표시한다.
 */
export function resetTokenFrom(input: string): string {
  const raw = input.trim();
  if (!raw.includes("token=")) return raw;
  try {
    // 상대 주소(`/password-reset#token=…`)도 읽도록 기준 주소를 준다. 기준은 값에 영향이 없다.
    const url = new URL(raw, "http://localhost");
    const token = tokenFromLocation(url.search, url.hash);
    return token?.trim() || raw;
  } catch {
    return raw;
  }
}

/**
 * 주소의 조각(`#token=…`)이나 쿼리(`?token=…`)에서 재설정 토큰을 꺼낸다. 없으면 null이다.
 *
 * 링크는 토큰을 조각에 싣는다. 조각은 브라우저가 서버로 보내지 않으므로 웹 서버 접근 로그와
 * 프록시 로그에 토큰이 남지 않는다. 쿼리는 조각으로 바꾸기 전에 발급한 링크를 읽으려고 함께 본다.
 */
export function tokenFromLocation(search: string, hash: string): string | null {
  const fromHash = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  if (fromHash) return fromHash;
  return new URLSearchParams(search).get("token") || null;
}

/**
 * 쿼리와 조각에서 `token`을 지운 주소(경로·쿼리·조각)를 만든다.
 *
 * 화면이 토큰을 읽은 뒤 주소창을 이 값으로 바꾼다. 주소창과 방문 기록에 토큰이 남으면 같은 기기를
 * 쓰는 사람이 기록에서 링크를 다시 연다. 다른 값은 그대로 둔다.
 */
export function addressWithoutToken(pathname: string, search: string, hash: string): string {
  const q = new URLSearchParams(search);
  q.delete("token");
  const h = new URLSearchParams(hash.replace(/^#/, ""));
  h.delete("token");
  const qs = q.toString();
  const hs = h.toString();
  return `${pathname}${qs ? `?${qs}` : ""}${hs ? `#${hs}` : ""}`;
}
