# shared/lib

## 목적

도메인과 무관한 표기·유틸을 둡니다. 이 코드는 어느 도메인의 용어도 모르고, 어느 화면에서 불러도 같은 값을 돌려줍니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `cn.ts` | Tailwind 클래스 병합(`clsx` + `tailwind-merge`) |
| `caption_length.ts` | 캡션 길이를 세는 단위의 설명(`CAPTION_LENGTH_UNIT`). 워크벤치의 제출 조건과 프로젝트 설정의 기준 안내가 같은 문장을 씁니다 |
| `edition.ts` | 제품 이름과 에디션 이름(`PRODUCT_NAME`·`EDITION_NAME`·`PRODUCT_FULL_NAME`). 워드마크, 사이드바 브랜드 줄, 탭 제목이 같은 값을 씁니다 |
| `time.ts` | 화면의 날짜·시각 표기. `whenLabel`(시점: 7일 안은 상대 시간, 그 뒤는 날짜), `relativeTime`(경과 시간), `fmtDateTime`(`9월 23일 18:42`), `fmtDate`, `fmtTime`, `dayGroupLabel`(날짜로 묶은 목록의 머리), `fmtFullDateTime`(툴팁용, 연도와 초 포함)을 둡니다. 24시간제를 쓰고, 올해 날짜는 연도를 생략합니다 |
| `ymd.ts` | `YYYY-MM-DD` 문자열과 로컬 `Date`를 서로 바꿉니다(`toYmd`·`parseYmd`). 주소에서 받은 날짜는 `isValidYmd`·`cleanYmdRange`로 걸러 냅니다. |
| `fmtSecs.ts` | 초 단위 값의 표기(`fmtSecs`·`fmtSecKo`·`fmtSpan`·`fmtClipRange`·`fmtTimecode`·`fmtClock`·`fmtH`). 영상 안 구간과 누적 시간을 같은 형식으로 씁니다 |
| `fmtBytes.ts` | 바이트 수, 비트레이트, 컨테이너 형식의 표기(`fmtBytes`·`fmtBitRate`·`fmtContainer`). 비트레이트는 1000 단위로 나눕니다 |
| `josa.ts` | 받침에 맞는 조사를 붙입니다(`hasFinalConsonant`·`withJosa`·`eulReul`) |
| `diff.ts` | 교정 diff(`wordDiff`·`countChanges`). 어절 단위로 맞춰 비교하고, 바뀐 어절 안에서는 글자 단위로 좁힙니다 |
| `download.ts` | 납품 패키지 받기(`downloadUrl`), 이미지 저장(`downloadImage`), 받은 파일 내용 저장(`saveBlob`, 메타 양식처럼 API가 인증을 받고 만드는 파일). `downloadUrl`은 서명된 링크의 내용을 페이지가 받아 저장합니다. `http` 페이지에서 같은 창 이동으로 받으면 100%에서 끝나지 않는 브라우저가 있기 때문입니다. 저장소가 읽기를 허용하지 않으면(CORS) 같은 창 이동(`startDownload`)으로 엽니다 |
| `uuid.ts`·`sha256.ts`·`clipboard.ts` | 보안 컨텍스트(https·localhost)에서만 제공되는 API의 대체 구현. `randomUuid`는 `getRandomValues`로, `sha256`은 스크립트 구현으로, `copyText`는 숨긴 입력란과 `execCommand("copy")`로 대신합니다. `isUuid`는 주소에서 받은 id의 형식을 서버에 보내기 전에 검사합니다 |

`clipboard`·`diff`·`fmtBytes`·`fmtSecs`·`josa`·`sha256`·`time`·`uuid`·`ymd` 옆에는 `<파일>.test.ts` 테스트가 있습니다.
`secure_apis.test.ts`는 소스를 읽어 화면 코드가 `crypto.randomUUID`·`crypto.subtle`·`navigator.clipboard`를 직접 부르지 않는지 확인합니다.

## 설계 이유

- 날짜 표기는 `time.ts` 한곳에서 정합니다. 화면이 `toLocaleString("ko-KR")`를 직접 쓰면 기본 형식(`2026. 9. 23. 오후 6:42:45`)이 섞여 화면마다 표기가 달라집니다.
  예외는 줄을 맞춰 대조하는 기록 화면(감사 로그·서버 로그)이고, 이 화면들은 `features/audit`의 `fmtEventTime`(`2026-09-23 18:42:45`)을 씁니다.
- 사람이 달력에서 고르는 날짜는 로컬 시간대(`ymd.ts`)를 씁니다.
  주소에서 받은 잘못된 날짜(`abc`·`2026-13-45`)가 `toISOString()`에 들어가면 예외가 나서 화면 전체가 열리지 않습니다. 그래서 쓰기 전에 걸러 냅니다.
- 운영 화면을 평문 http로 열면 `crypto.randomUUID`·`crypto.subtle`·`navigator.clipboard`가 `undefined`입니다. localhost는 보안 컨텍스트라서 개발 환경에서는 이 문제가 드러나지 않습니다.
  그래서 화면은 이 세 API 대신 이 폴더의 함수를 쓰고, `secure_apis.test.ts`가 직접 호출을 막습니다.
  `sha256`이 환경마다 다른 값을 내면 서버의 업로드 중복 감지가 같은 파일을 놓칩니다. 그래서 테스트가 Node 구현과 결과를 대조합니다.
- 캡션 토큰 근사(`features/workbench/lib/tokens.ts`)는 이름은 범용처럼 보이지만, 서버와 같은 판정 표를 읽는 도메인 규칙이라 이 폴더에 두지 않습니다.

## 의존성

- 부르는 것: `clsx`·`tailwind-merge`(`cn`), `react`(`useCountdown`). `shared/` 밖은 가져오지 않습니다.
- 부르는 쪽: 앱 전체

## 남은 일

- 없음.
