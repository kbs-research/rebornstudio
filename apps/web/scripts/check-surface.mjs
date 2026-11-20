/**
 * 표준 부품이 있는데 화면이 그 클래스를 손으로 베껴 쓰는 경우를 센다.
 *
 * 손으로 베낀 클래스도 정상적으로 컴파일되고 자리표시 값으로 쓴 수는 타입이 `number`라서
 * `tsc`와 `vite build`는 이 문제를 잡아내지 못한다. 화면을 열어야 보이고, 열어도 보이지 않는 경우도 있다.
 * 이 게이트는 아래 두 경우를 센다.
 *
 * 첫째, 손으로 베낀 카드.
 * `Card`는 `rounded-card border border-separator bg-panel shadow-e1`이다. 화면이 이 클래스를
 * 손으로 적으면 마지막 `shadow-e1`을 빠뜨리기 쉽고, 그러면 같은 "페이지 위의 카드"가 화면마다
 * 떠 보이거나 평평해진다.
 * 절을 카드로 그리려면 `Card.as` 슬롯을 쓴다. 슬롯이 있는데도 베끼려면 그 이유를 아래 `FLAT_OK`에 적는다.
 *
 * 둘째, 자리표시 값으로 쓴 수가 실제 값처럼 화면에 표시되는 것.
 * - `Math.min(a || Infinity, b || Infinity)`: 둘 다 없으면 `Infinity`가 남는다. 받는 쪽이
 *   `new Date(Infinity)`를 표시해 화면에 `마지막 업데이트 Invalid Date`가 뜬다.
 * - `value: (r) => r.x ?? Number.MAX_SAFE_INTEGER`: 표본 없는 사람이 내림차순 맨 위로
 *   올라와 "가장 느린 사람"처럼 읽힌다.
 * 두 번째 규칙은 `shared/ui/DataTable.tsx`의 `value` docstring에도 적혀 있다.
 * 문서에만 있고 게이트가 없는 규칙은 다시 어겨지므로 이 게이트를 둔다.
 *
 * 사용법:  node scripts/check-surface.mjs
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

/** 디렉터리를 재귀로 돌며 `.ts`/`.tsx` 파일(테스트 제외)을 모은다. */
function walk(dir) {
  let out = [];
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) out = out.concat(walk(p));
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p);
  }
  return out;
}

/**
 * 카드 클래스를 손으로 쓰되 평평해도 되는 파일 목록. 파일 단위로 적고 이유는 필수다.
 *
 * 줄 번호는 다음 편집에서 바로 바뀌고 어긋난 목록은 아무도 고치지 않으므로 파일 단위로 적는다.
 * 이유 없이 추가하면 다음 사람이 지워도 되는지 알 수 없으므로 이유를 함께 적는다.
 */
const FLAT_OK = {
  "routes/design_system.tsx":
    "갤러리 — 층 표본을 나란히 늘어놓는 자리라 그림자 없는 면이 **표본 자체**다",

  // ── 드로어 안: 드로어가 이미 `shadow-e3`라서 그 위에 또 띄우면 면 위에 면이 겹친다. ──
  "features/pipeline/pages/qc.tsx": "보류·실행 상세 드로어 안의 캡션 상자와 접힌 기술 정보 — 검사 항목 탭의 카드 목록은 `Card`다",

  // ── 카드 안. ──
  "features/workbench/components/SentenceEditor.tsx": "문장 카드 안의 `수정 전 대비` 비교 상자",

  // ── 카드 이외의 윤곽. ──
  "shared/ui/EmptyState.tsx": "`framed` 빈 상태의 점선 상자 — 목록이 놓일 곳의 윤곽이라 띄우지 않는다",
};

/** 파일이 `FLAT_OK`에 있으면 true를 돌려준다. 키는 `src/` 아래 상대 경로의 접미사다. */
const flatOk = (rel) => Object.keys(FLAT_OK).find((k) => rel.endsWith(k));

// 손으로 쓴 카드 클래스, 정렬 값의 자리표시 값, 없음을 큰 수로 적는 코드를 찾는 패턴.
const CARD_HAND = /rounded-card[^"'`]*border-separator[^"'`]*bg-panel/;
const SORT_SENTINEL = /value:\s*\([^)]*\)\s*=>[^,\n]*\?\?\s*(-?\d+|Number\.[A-Z_]+|Infinity)/;
const INFINITY_PLACEHOLDER = /(\|\||\?\?)\s*(Infinity|Number\.MAX_SAFE_INTEGER)\b/;

/**
 * 주석을 지우되 줄 수는 그대로 둔다. 줄 번호를 정확히 출력해야 고칠 위치를 찾을 수 있다.
 *
 * 이 저장소의 주석은 `{/* … *\/}` 블록이 여러 줄에 걸치고 이어지는 줄이 `*`로 시작하지 않아서,
 * 줄 머리만 보는 방식(`/^\s*(\/\/|\*)/`)은 주석을 놓친다.
 * 규칙을 설명한 주석이 위반으로 잡히면 다음 사람은 규칙을 적지 않게 된다.
 */
function stripComments(src) {
  let out = "";
  let inBlock = false;
  for (const line of src.split("\n")) {
    let kept = "";
    for (let i = 0; i < line.length; i++) {
      if (inBlock) {
        if (line[i] === "*" && line[i + 1] === "/") { inBlock = false; i++; }
        continue;
      }
      if (line[i] === "/" && line[i + 1] === "*") { inBlock = true; i++; continue; }
      if (line[i] === "/" && line[i + 1] === "/") break;
      kept += line[i];
    }
    out += kept + "\n";
  }
  return out.split("\n");
}

const cards = [];
const sentinels = [];
const placeholders = [];
let allowed = 0;

for (const file of walk(SRC)) {
  const rel = file.slice(file.indexOf("src/"));
  // `Card` 자신은 표준 부품이라 검사 대상에서 뺀다.
  const isCardItself = rel.endsWith("shared/ui/Card.tsx");

  stripComments(readFileSync(file, "utf8")).forEach((line, i) => {
    const at = `${rel}:${i + 1}`;

    if (!isCardItself && CARD_HAND.test(line) && !/shadow-/.test(line)) {
      if (flatOk(rel)) allowed++;
      else cards.push([at, "`Card`를 손으로 베꼈고 `shadow-e1`이 빠졌습니다"]);
    }
    if (SORT_SENTINEL.test(line)) {
      sentinels.push([at, "정렬 값에 자리표를 골랐습니다 — `?? null` 로 줍니다"]);
    }
    if (INFINITY_PLACEHOLDER.test(line)) {
      placeholders.push([at, "없음을 큰 수로 적으면 표시로 샙니다 — 있는 값만 모읍니다"]);
    }
  });
}

const findings = [...cards, ...sentinels, ...placeholders];
if (findings.length) {
  console.log(`표면 정본을 안 쓰는 자리 ${findings.length}건:\n`);
  for (const [at, why] of findings) console.log(`  ${at}\n      ${why}`);
  console.log(
    "\n  → 카드는 `<Card as=…>`, 정렬 값은 `?? null`, 없음은 자리표 대신 안 넘깁니다.",
  );
  console.log("     평평한 것이 맞으면 `FLAT_OK` 에 **이유와 함께** 적습니다.");
  process.exit(1);
}
console.log(
  `✅ 손으로 베낀 카드 없음(이유 달고 허용 ${allowed}) · 정렬 자리표 없음 · 없음을 큰 수로 적는 자리 없음`,
);
