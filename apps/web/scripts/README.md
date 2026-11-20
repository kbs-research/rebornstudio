# apps/web/scripts

## 목적

웹 소스(`src/`)를 읽어 `tsc`와 `vite build`가 잡지 못하는 버그를 찾는 정적 검사를 둡니다.
없는 유틸, 손으로 베낀 카드, 조회 실패를 빈 목록으로 그리는 화면, 이름 없는 입력란은 모두 오류 없이 컴파일됩니다.
그래서 화면을 열어 보기 전에 소스의 모양으로 찾습니다. 대부분은 CI lint 잡(`.github/workflows/ci.yml`)이 실행합니다.

## 주요 파일

| 파일 | 역할 | CI 호출 |
|---|---|---|
| `check-headings.mjs` | 절 제목 모양(`font-semibold` + 글자 단계 + `text-label*`)의 요소가 heading 요소인지, 화면마다 `h1`이 하나인지 검사합니다 | `--strict` |
| `check-query-states.mjs` | 조회를 열고 빈 상태를 그리면서 실패(`isError`·`ErrorState`)를 구분하지 않는 파일을 셉니다 | `--strict` |
| `check-danger-copy.mjs` | 비가역 액션 확인창(`variant="danger"` 버튼이나 삭제·제거 같은 제목)의 본문에 결과를 알리는 문장이 있는지 검사합니다 | `--strict` |
| `check-a11y-names.mjs` | `placeholder`만 있고 접근 가능한 이름(`aria-label`·`aria-labelledby`·`id`)이 없는 입력란을 셉니다 | `--strict` |
| `check-false-signals.mjs` | 행 인자를 받지 않는 표 열(`cell: () =>`)과, `body`에서 상속되는 줄바꿈 유틸을 요소에 다시 붙인 곳을 셉니다 | 기본 |
| `check-surface.mjs` | `Card` 클래스를 손으로 베낀 곳과, 값이 없을 때 대신 넣은 수(`Infinity`·`Number.MAX_SAFE_INTEGER`)가 화면 표시나 정렬에 드러나는 곳을 셉니다 | 기본 |
| `check-orphan-docstrings.mjs` | 코드 없이 연달아 붙은 docstring 두 개 중 대상이 없는 앞의 것을 찾습니다 | 기본 |
| `check-copy.mjs` | 화면 문구 규칙 가운데 기계로 판정할 수 있는 부분을 검사합니다. 마크다운, 1인칭·`벤더`, 표기 불일치, 반말 종결 등이 대상입니다 | 기본(`apps/web`에서 실행) |
| `check-jargon.mjs` | 화면 문구에 내부 용어 사전의 낱말이 있는지 검사합니다 | `--strict` |
| `check-feature-boundaries.mjs` | 기능 폴더 경계 규칙(`api()` 위치, 키 팩토리, 다른 기능은 `index.ts`로만, `shared/`의 바깥 import, 옛 경로)의 위반을 셉니다. `--list`는 파일별 수를 출력합니다 | `--strict` |
| `check-utilities.mjs` | 소스가 쓰는 시맨틱 유틸이 빌드 CSS에 실제로 있는지, 타입 스케일·모서리 이름 밖의 유틸을 쓰지 않았는지 검사합니다 | 없음(`make check-utilities`) |

## 설계 이유

- 허용 목록이 있는 검사(`EXPECTED`·`H1_EXPECTED`·`FLAT_OK`)는 목록에 없는 새 위반만 막습니다. 허용 항목에는 사유를 적습니다.
  사유가 없으면 다음 사람이 그 항목을 지워도 되는지 판단할 수 없습니다.
  `EXPECTED`를 쓰는 검사는 더는 걸리지 않는 허용 항목을 낡은 항목으로 따로 출력합니다.
- 판정은 놓치는 쪽으로 기울게 둡니다. 오탐이 잦은 게이트는 사람들이 무시하게 되고, 그러면 진짜 위반도 통과합니다.
- 대부분의 검사는 주석을 지운 소스를 읽습니다. 주석이 예시로 적은 클래스나 `api(` 호출을 세지 않기 위해서입니다.
- `check-utilities.mjs`는 `dist/assets`의 CSS를 읽으므로 빌드를 먼저 해야 합니다. 그래서 CI lint 잡에 넣지 않고, PR 전에 `make check-utilities`로 실행합니다.
  빌드 CSS가 소스보다 오래됐으면 먼저 경고합니다.

## 의존성

- Node 내장 모듈(`node:fs`·`node:path`)만 씁니다. 패키지를 설치하지 않아도 실행됩니다.
- 읽는 것: `apps/web/src/**`. `check-utilities.mjs`는 `apps/web/dist/assets`도 읽습니다.
- 부르는 쪽: CI lint 잡, `scripts/ci/verify.sh`(`make verify`), `Makefile`의 `check-utilities` 대상.
- 저장소 루트 `scripts/ci/`에는 웹과 서버를 함께 읽는 검사(`check-missing-routes.mjs`·`check-orphan-routes.mjs` 등)가 있습니다([`scripts/ci/README.md`](../../../scripts/ci/README.md)).

## 남은 일

- `check-a11y-names.mjs`의 두 번째 검사(반복 요소의 고정 이름)는 `--strict`에서도 건수만 보여 줍니다. 남은 항목을 화면에서 확인해 정리하면 `--strict`로 막을 수 있습니다.
