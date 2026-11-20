# features/auth/lib

## 목적

로그인 전 화면의 순수 규칙과 고지 문구를 둡니다. 화면 부품과 분리해서 렌더 없이 테스트하고, 여러 화면이 같은 값을 읽게 합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `password_policy.tsx` | 비밀번호 규칙 다섯 가지(`PASSWORD_RULES`), 충족 판정 `meetsPasswordPolicy`, 입력 중에 보이는 체크 목록 `PasswordRules`. 서버 `api_gateway/security.py`의 `validate_password_policy`와 같은 규칙입니다 |
| `privacy_terms.ts` | 개인정보 수집 항목 표 `COLLECTED`, 이용 종료의 뜻 `USE_END_NOTE`, 필수 동의 체크 문구(`REQUIRED_CONSENT_LABEL`), 항목 종류별 색조 `KIND_TONE`. 안내 화면·동의 화면·최초 설정 화면이 읽습니다 |
| `reset_token.ts` | 재설정 입력란에 붙여 넣은 값에서 토큰을 꺼냅니다(`resetTokenFrom`). 링크 전체를 붙여 넣어도 `token` 값만 쓰고, 앞뒤 공백과 줄바꿈을 지웁니다. 링크로 들어온 주소의 조각(`#token=`)과 옛 쿼리(`?token=`)에서 토큰을 꺼내고(`tokenFromLocation`), 토큰을 지운 주소를 만듭니다(`addressWithoutToken`). 조각은 서버로 가지 않아 접근 로그에 남지 않습니다 |
| `safe_next.ts` | 로그인 뒤 돌아갈 주소(`?next=`)가 앱 안의 경로일 때만 그 경로를 돌려줍니다(`safeNext`). 바깥 주소·역슬래시·제어 문자(탭·줄바꿈)가 든 값과 로그인 화면 자신은 홈으로 바꿉니다. 브라우저가 주소의 탭과 줄바꿈을 지우므로 `/\t/evil.example`은 `//evil.example`로 읽힙니다 |
| `safe_next.test.ts` | 바깥 주소와 제어 문자가 든 값이 홈으로 바뀌는지 확인하는 테스트 |
| `reset_token.test.ts` | 링크·토큰·공백이 섞인 값에서 같은 토큰이 나오는지 확인하는 테스트 |

## 설계 이유

- 화면은 서버와 같은 비밀번호 규칙을 입력하는 동안 보여 줍니다. 다 입력한 뒤 422로 거절당하는 것보다 미리 아는 편이 낫습니다.
- 동의 화면은 동의를 받기 전에 수집 항목과 보유 기간을 보입니다. 안내 화면과 동의 화면이 내용을 각자 적으면 동의한 조건과 고지가 달라집니다. 그래서 표를 한 파일에 둡니다.
- 안내 문구가 받은 링크를 붙여 넣으라고 하므로 입력란은 링크 전체를 받습니다. 링크를 그대로 보내면 서버는 토큰으로 읽지 못합니다.

## 의존성

- `lucide-react`(체크 목록 아이콘), `shared/lib/cn`, `shared/ui/Chip`(색조 타입)
- 부르는 쪽: `../pages/*`, `features/me`(`index.ts`를 거쳐 비밀번호 규칙)

## 남은 일

- 없음.
