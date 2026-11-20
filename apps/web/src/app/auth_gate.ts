/**
 * 보호 셸의 인증 가드 판정.
 *
 * 셸을 그릴지, 연결 실패 패널을 그릴지, 어느 로그인 주소로 보낼지를 이 함수 한 곳에서 정한다.
 * 로그아웃한 탭과 신호를 받은 다른 탭이 서로 다른 순서로 다시 그려져도 같은 주소로 끝나게 하려고
 * 화면 이동을 이벤트 처리 순서가 아니라 세션 상태로 정한다.
 */

/** 가드가 읽는 세션 상태와 현재 주소. */
export interface AuthGateInput {
  /** 메모리에 access 토큰이 있으면 참. */
  authed: boolean;
  /** 세션 만료 창을 띄운 동안 참. 이때는 셸을 내리지 않고 비밀번호만 다시 받는다. */
  sessionExpired: boolean;
  /** 이 탭이나 다른 탭에서 사용자가 로그아웃해 세션이 끝났으면 참. */
  signedOut: boolean;
  /** 앱 시작 때 세션 복구가 서버 일시 오류로 끝났으면 참. */
  restoreUnavailable: boolean;
  pathname: string;
  search: string;
}

/** 가드 판정 결과. `login`의 `to`는 이동할 로그인 주소다. */
export type AuthGate = { kind: "shell" } | { kind: "restore-failed" } | { kind: "login"; to: string };

/**
 * 돌아올 주소를 붙인 로그인 경로를 만든다.
 *
 * `/login`만 쓰면 다시 로그인했을 때 늘 홈으로 간다.
 * 그러면 북마크로 연 화면에도, 세션이 끊긴 작업 화면에도 돌아갈 수 없다.
 * 받는 쪽(`login.tsx`)은 앱 안의 경로만 따른다.
 */
export function loginPath(pathname: string, search: string): string {
  const here = `${pathname}${search}`;
  return here === "/" ? "/login" : `/login?next=${encodeURIComponent(here)}`;
}

/**
 * 보호 셸이 무엇을 그릴지 정한다.
 *
 * 로그아웃으로 끝난 세션은 만료 창이나 복구 실패보다 먼저 보고 돌아올 주소 없이 `/login`으로 보낸다.
 * 사용자가 직접 끝낸 세션이라 이전 화면으로 돌아갈 이유가 없다.
 * 그 밖에 토큰이 없으면 만료 창을 띄운 동안은 셸을 두고, 복구가 일시 오류면 연결 실패 패널을,
 * 아니면 돌아올 주소를 붙인 로그인 화면을 고른다.
 */
export function authGate(input: AuthGateInput): AuthGate {
  if (input.authed) return { kind: "shell" };
  if (input.signedOut) return { kind: "login", to: "/login" };
  if (input.sessionExpired) return { kind: "shell" };
  if (input.restoreUnavailable) return { kind: "restore-failed" };
  return { kind: "login", to: loginPath(input.pathname, input.search) };
}
