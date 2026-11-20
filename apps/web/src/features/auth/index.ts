/**
 * 인증 기능의 공개 진입점. 다른 기능은 이 파일로만 가져온다.
 *
 * 비밀번호 규칙은 `내 계정`의 비밀번호 변경도 같이 쓴다. 동의 화면(`PrivacyConsentPage`)은 보호 셸이 동의 기록 없는 계정에 셸 대신 그린다. 라우트(`routes.tsx`)는 라우터만 쓰므로 이 파일에서 내보내지 않는다.
 */
export { fetchMe, login } from "./api";
export { PrivacyConsentPage } from "./pages/consent";
export { meetsPasswordPolicy, PasswordRules } from "./lib/password_policy";
