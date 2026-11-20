# features/auth

## 목적

로그인 전에 여는 화면과 개인정보 동의 화면을 담당합니다. 로그인(`/login`), 비밀번호 재설정(`/password-reset`),
최초 설정(`/setup`), 개인정보 수집·이용 안내(`/privacy`)는 보호 셸 밖에서 로그인 없이 열립니다.
동의 화면(`PrivacyConsentPage`)은 주소가 없습니다. 동의 기록이 없는 계정이 로그인하면 보호 셸이 셸 대신 이 화면을 그립니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 쿼리 키 `authKeys`. 셋업 상태·로그인 계정 조회·동의 기록(`POST /users/me/privacy-consent`)·로그인·재설정 링크 확인과 확정·첫 최고관리자 생성 |
| `routes.tsx` | 라우트 배열 `authRoutes`. `app/router.tsx`가 라우트 표 맨 앞(보호 셸 밖)에 펼칩니다 |
| `index.ts` | 밖으로 내보내는 것: `fetchMe`(`store/auth`), `login`(보호 셸의 다시 로그인 창), `PrivacyConsentPage`(보호 셸), `meetsPasswordPolicy`·`PasswordRules`(내 계정의 비밀번호 변경) |
| [`pages/`](./pages/README.md) | 화면 여섯 개 |
| [`components/`](./components/README.md) | 인증 셸과 안내 상자 |
| [`lib/`](./lib/README.md) | 비밀번호 규칙, 개인정보 수집 항목 표와 동의 문구, 재설정 토큰 추출 |

## 설계 이유

- 토큰 저장·갱신(`shared/api/auth`)은 로그인 뒤의 모든 화면과 API 클라이언트가 씁니다. 그래서 이 기능에 두지 않습니다.
- 서버는 동의 기록이 없는 계정의 업무 요청을 403 `privacy_consent_required`로 막습니다(`apps/api-gateway/api_gateway/services/auth/consent.py`).
  이때 셸을 그리면 화면마다 403이 납니다. 그래서 보호 셸이 셸 대신 동의 화면 하나를 그립니다.
- 비밀번호를 만드는 세 화면(재설정·최초 설정·내 계정)이 같은 비밀번호 규칙을 씁니다. 규칙을 한 곳에 두지 않으면 화면마다 같은 규칙을 다르게 안내합니다.
- 쿼리 키 배열 값(`["password-reset-check", token]`)과 요청 경로·본문은 바꾸지 않습니다. 서버 계약과 캐시 무효화가 이 값에 의존합니다.

## 의존성

- 부르는 것: `shared/api/client`·`shared/api/auth`·`shared/api/error_text`, `shared/ui/*`, `shared/hooks/useDocumentTitle`, `app/queryClient`, `store/auth`,
  `features/audit`(`fmtEventTime`, 재설정 링크 만료 시각)
- 부르는 쪽: `app/router.tsx`(`authRoutes`), `app/_layout.tsx`(`login`·`PrivacyConsentPage`), `store/auth.ts`(`fetchMe`), `features/me`(비밀번호 규칙)

## 남은 일

