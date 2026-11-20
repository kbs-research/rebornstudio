/**
 * 인증 화면(로그인·비밀번호 재설정·최초 설정·동의)의 서버 호출과 쿼리 키.
 *
 * 화면은 경로 문자열을 직접 쓰지 않고 이 파일의 함수를 부른다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 토큰 저장·갱신은 로그인 뒤의 모든 화면이 쓰므로 이 파일 대신 `shared/api/auth`에 둔다.
 */
import type { components } from "@reborn/contracts";

import { api, type LoginResponse, type UserDTO } from "@/shared/api/client";

/** 재설정 링크 상태 확인의 응답. 링크의 유효 여부와 만료 시각, 발급 뒤 유효 시간(분)이 있다. */
export type ResetStatus = components["schemas"]["PasswordResetStatus"];

/** 최초 설정에서 첫 최고관리자를 만드는 요청 본문. `token`은 서버 로그의 셋업 토큰이다. 개인정보 수집·이용 동의는 필수다. */
export interface SetupCompleteBody {
  email: string;
  display_name: string;
  password: string;
  token: string;
  privacy_consent: boolean;
}

/**
 * 인증 화면의 쿼리 키.
 *
 * 링크 상태는 토큰마다 따로 캐시한다.
 */
export const authKeys = {
  passwordResetCheck: (token: string) => ["password-reset-check", token] as const,
};

/** 셋업이 필요한지 조회한다. 인증 없이 열리고 `{uninitialized, token_required}`만 돌려준다. */
export const fetchSetupStatus = () => api<{ uninitialized: boolean }>("/setup/status");

/** 로그인한 사람의 계정을 받는다. 로그인 스토어(`store/auth`)가 앱을 열 때와 다시 로그인한 뒤에 부른다. */
export const fetchMe = () => api<UserDTO>("/auth/me");

/**
 * 로그인한 사람의 필수 동의를 기록한다. 동의 화면이 부른다.
 *
 * 동의 전에는 업무 요청이 403 `privacy_consent_required`라서 이 호출과 `fetchMe`만 쓸 수 있다.
 * 이미 동의한 항목은 서버가 바꾸지 않고 현재 계정을 돌려준다.
 */
export const consentPrivacy = () =>
  api<UserDTO>("/users/me/privacy-consent", { method: "POST" });

/**
 * 이메일과 비밀번호로 로그인한다.
 *
 * 이 요청의 401은 비밀번호가 틀렸다는 뜻이라 세션 만료로 읽지 않도록 오류로 받는다(`expect401`).
 * 정지된 계정이면 403 `account_suspended`가 온다.
 */
export const login = (body: { email: string; password: string }) =>
  api<LoginResponse>("/auth/login", {
    method: "POST",
    expect401: true,
    body: JSON.stringify(body),
  });

/** 재설정 링크를 쓸 수 있는지 확인한다. 토큰은 주소 대신 본문으로 보낸다. 빈 토큰이면 유효 시간만 받는다. */
export const checkPasswordReset = (token: string) =>
  api<ResetStatus>("/auth/password/reset/check", { method: "POST", body: JSON.stringify({ token }) });

/** 재설정 링크의 토큰으로 새 비밀번호를 정한다. 토큰은 한 번만 쓸 수 있다. */
export const confirmPasswordReset = (token: string, newPassword: string) =>
  api<void>("/auth/password/reset/confirm", {
    method: "POST", body: JSON.stringify({ token, new_password: newPassword }),
  });

/** 첫 최고관리자를 만든다. 세션은 만들지 않는다. 계정은 방금 정한 비밀번호로 로그인해 확인한다. */
export const completeSetup = (body: SetupCompleteBody) =>
  api("/setup/complete", {
    method: "POST",
    body: JSON.stringify(body),
  });
