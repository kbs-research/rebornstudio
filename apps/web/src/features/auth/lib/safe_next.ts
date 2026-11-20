/**
 * `?next=`가 앱 안의 경로일 때만 그 경로를 돌려주고, 아니면 홈(`/`)을 돌려준다.
 *
 * 셸이 로그인 화면으로 보낼 때 돌아올 주소를 붙인다(`_layout`의 `loginPath`).
 * 바깥 주소(`//evil.example`, `https:`)나 로그인 화면 자신으로는 보내지 않는다.
 * 보내면 열린 리다이렉트가 된다.
 * 제어 문자가 든 값도 홈으로 보낸다. 브라우저는 주소의 탭과 줄바꿈을 지우므로 `/\t/evil.example`은
 * 검사를 통과한 뒤 `//evil.example`로 읽힌다.
 */
export function safeNext(raw: string | null): string {
  if (!raw || /[\u0000-\u001f\u007f]/.test(raw)) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";
  if (raw.startsWith("/login")) return "/";
  return raw;
}
