/**
 * 조회 실패를 "없습니다"로 그리는 화면을 센다.
 *
 * 비어 있는 상태와 불러오지 못한 상태를 구분한다. 실패를 0으로 그리면 사용자는 할 일이 없다고
 * 읽고 화면을 닫는다. 전역 토스트는 5초 뒤 사라지고 본문의 잘못된 표시는 남는다.
 * 실패를 구분하지 않는 코드도 정상적으로 컴파일되므로 `tsc`와 `vite build`는 이 문제를 잡아내지 못한다.
 *
 * 검사 대상: 한 파일이 다음 세 가지에 모두 해당하면 후보다.
 * 1. 질의를 연다: `useQuery(` · `useInfiniteQuery(`
 * 2. 빈 상태를 그린다: `<EmptyState` · `empty=`
 * 3. 실패를 구분하는 신호(`isError` · `.error` · `error=` · `<ErrorState`)가 없다.
 *
 * 네 번째 경우는 실패를 아무 일도 아닌 것처럼 처리하는 코드라 빈 상태 조건 없이 따로 센다.
 * 4. `error) return null`
 * `error`에는 403 외에 500과 타임아웃도 같은 경로로 온다. 그때 카드가 알림 없이 사라지고 페이지는 멀쩡해 보인다.
 * 잘못된 "없습니다" 표시는 읽히기라도 하지만 사라진 카드는 흔적이 남지 않아 더 찾기 어렵다.
 *
 * 403만 걸러 내는 코드는 정당하므로 `isForbidden(`을 쓰면 통과시킨다.
 * 판정을 한 곳(`shared/api/client.ts`의 `isForbidden`)에 모으려는 목적도 있다.
 *
 * 구조분해로 받은 `error`는 이름이 흔해서(`onError`·`error` 변수) 정규식으로 구분할 수 없으므로,
 * 그 파일이 표준 실패 컴포넌트(`<ErrorState`)를 그리는지를 신호로 쓴다.
 * 그러면 `const { …, error }`와 `if (error)` 형태로 실패를 이미 처리하는 파일이 오탐으로 걸리지 않는다.
 *
 * 한계: 파일 단위라 판정이 거칠어서, 한 파일에 질의가 여럿이면 하나만 실패를 구분해도 통과한다.
 * 놓칠 수는 있어도 오탐은 나지 않는 방향이다. 0건은 이 검사에 걸린 것이 없다는 뜻일 뿐이고,
 * 정밀한 판정은 사람이 그 파일을 열어서 한다.
 *
 * CI에서는 `--strict`로 막는다. 빈 상태가 권한이나 계산 결과인 경우(역할이 없어 액션이 0개, 단계 접근 권한 없음)는
 * 기계가 구분할 수 없지만 `EXPECTED`에 이유와 함께 등록해 예외로 둘 수 있다.
 * 새 경우가 생길 때 작성자가 정하는 편이 나중에 훑어서 찾는 것보다 비용이 적다.
 * 옵션 없이 실행하면 세기만 하므로 이미 있는 경우를 훑을 때 쓴다.
 *
 * 사용법:  node scripts/check-query-states.mjs
 *          node scripts/check-query-states.mjs --strict   (목록에 없는 항목이 있으면 exit 1)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

/** 빈 상태가 조회 결과가 아닌 파일 목록. 파일별로 이유를 적는다. */
const EXPECTED = {
  "features/pipeline/pages/pipeline.tsx":
    "`작업 단계 접근 권한이 없습니다` — 권한 상태다. 목록이 비어서가 아니다",
};

const OPENS_QUERY = /\buseQuery\(|\buseInfiniteQuery\(/;
const DRAWS_EMPTY = /<EmptyState|\bempty=\{/;
const HANDLES_ERROR = /\bisError\b|\.error\b|\berror=\{|<ErrorState/;
/* 실패 시 아무것도 그리지 않는 코드를 찾는다. `isForbidden(...)`으로 403만 거르는 코드는
   정당하므로 그 줄은 세지 않고, 아래에서 줄 단위로 구분한다. */
const VANISHES_ON_ERROR = /error\)\s*return null/;

/** 디렉터리를 재귀로 돌며 `.tsx` 파일 경로를 모은다. */
function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (e.endsWith(".tsx")) out.push(p);
  }
  return out;
}

const suspects = [];
const vanishing = [];
for (const file of walk(SRC)) {
  const src = readFileSync(file, "utf8");
  const rel = file.slice(SRC.length + 1);

  // 한 파일에 `isForbidden` 분기와 `error`만 보는 분기가 함께 있을 수 있어 4번은 줄 단위로 검사한다.
  if (OPENS_QUERY.test(src)) {
    for (const line of src.split("\n")) {
      if (VANISHES_ON_ERROR.test(line) && !line.includes("isForbidden(")) {
        vanishing.push(`${rel}  ${line.trim()}`);
      }
    }
  }

  if (!OPENS_QUERY.test(src) || !DRAWS_EMPTY.test(src)) continue;
  if (HANDLES_ERROR.test(src)) continue;
  suspects.push(rel);
}

const unexpected = suspects.filter((rel) => !(rel in EXPECTED));
const stale = Object.keys(EXPECTED).filter((rel) => !suspects.includes(rel));

if (unexpected.length) {
  console.error(`\n⚠️  질의를 여는데 실패를 안 가르고 빈 상태를 그리는 파일 ${unexpected.length}건\n`);
  for (const rel of unexpected) console.error(`   ${rel}`);
  console.error(
    "\n   그 파일을 열어서 정한다 — 표면 표라면 `error={q.isError}` 한 줄,",
  );
  console.error("   아니면 `q.isError ? <ErrorState onRetry=… /> : <EmptyState … />`.");
  console.error("   빈 상태가 조회 결과가 아니면 EXPECTED 에 이유와 함께 적는다.");
} else if (!vanishing.length) {
  console.log(
    `✅ 조회 실패를 '없습니다'로 그리는 자리 없음 · 실패에 사라지는 자리 없음 ·`
      + ` 조회 결과가 아닌 빈 상태 ${suspects.length}건은 사유 등록됨`,
  );
}

if (vanishing.length) {
  console.error(`\n⚠️  실패에 **카드가 통째로 사라지는** 자리 ${vanishing.length}건\n`);
  for (const v of vanishing) console.error(`   ${v}`);
  console.error(
    "\n   `error` 는 403 만이 아니다 — 500·타임아웃도 여기로 온다. 권한만 걸러 내려면",
  );
  console.error("   `isForbidden(q.error)` 를 쓰고, 그 밖의 실패는 `<ErrorState onRetry=… />` 로 말한다.");
}
if (stale.length) {
  console.log(`\n⚠️  EXPECTED 에 있는데 더는 해당 없는 파일 ${stale.length}건 — 지워도 된다`);
  for (const rel of stale) console.log(`   ${rel}`);
}

process.exit(
  process.argv.includes("--strict") && (unexpected.length || vanishing.length) ? 1 : 0,
);
