/**
 * 비가역 액션의 확인창이 누르면 일어나는 일을 설명하는지 검사한다.
 *
 * 비가역 액션에는 판단 근거를 같은 화면에 둔다.
 * 문구가 빠져도 문법은 맞으므로 `tsc`와 `check-copy`는 이 문제를 잡아내지 못한다.
 * 예: `멤버 제거`가 한 줄뿐이면 그 사람이 가져간 작업이 어떻게 되는지 누르는 사람이 알 수 없다.
 * `프로젝트 삭제`는 soft delete라서 영상과 작업이 남는다는 사실을 설명해야 한다.
 *
 * 위험한 확인창의 판정: `<Modal>` 블록이 다음 중 하나에 해당하면 확인창으로 본다.
 * 1. 안에 `variant="danger"` 버튼이 있다.
 * 2. 제목에 삭제·제거·폐기·거절·정지·회수·나가기·재투입·확정이 들어 있다.
 *
 * 통과 조건: 본문(버튼 라벨과 제목을 뺀 나머지)에 결과를 설명하는 표지가 하나라도 있으면 통과다.
 * 표지는 `됩니다`·`집니다`·`되돌릴`·`사라지`·`지워지`·`돌아갑`·`빠집`·`초기화`·`수 없`·`영구` 등이다.
 * 버튼 라벨을 빼지 않으면 `취소`·`삭제` 두 글자로 통과하므로 본문에서 뺀다.
 *
 * 이 검사의 한계는 세 가지다.
 * 1. 문장이 있는지만 확인하고 그 문장이 사실인지는 확인하지 않는다. `프로젝트 삭제`가 "영구
 *    삭제됩니다"라고 잘못 써도 통과하므로, 서버가 실제로 하는 일은 사람이 코드를 열어 확인한다(`soft_delete_project`).
 * 2. 표지 목록으로 판정해서 결과를 다른 말로 쓰면 놓치고, 위험하지 않은데 표지가 있으면 오탐이 난다.
 *    오탐이 잦은 게이트는 곧 무시되므로 판정은 놓치는 쪽으로 기울여 둔다.
 * 3. 위험 판정도 휴리스틱이다. 제목이 `정리`·`비우기` 같은 말이면 걸리지 않는다.
 *
 * 따라서 이 검사는 결과 설명이 아예 없는 경우만 막고, 통과한 확인창 문구가 맞는지는 사람이 확인한다.
 *
 * 사용법:  node scripts/check-danger-copy.mjs
 *          node scripts/check-danger-copy.mjs --strict   (목록에 없는 항목이 있으면 exit 1, CI용)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

/**
 * 결과 설명을 생략해도 되는 확인창 목록. 키는 `파일 :: 제목` 형식이고 값에 이유를 적는다.
 *
 * 항목을 넣을 때는 화면을 열어 확인한 뒤 적는다. 목록이 길어지면 이 검사가 막는 경우가 줄어든다.
 */
const EXPECTED = {};

/** 확인창으로 판정할 제목의 낱말. */
const DANGER = /삭제|제거|폐기|거절|정지|회수|나가기|재투입|확정/;

/** 누르면 일어나는 일을 설명하는 표지. */
const EFFECT =
  /됩니다|집니다|갑니다|남습니다|되돌릴|되살릴|사라지|지워지|지워집|돌아갑|빠집|초기화|수 없|못 [가-힣]|영구/;

/** 디렉터리를 재귀로 돌며 `.tsx` 파일 경로를 모은다. */
function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (e.endsWith(".tsx")) out.push(p);
  }
  return out;
}

/**
 * `<Modal …>`부터 짝이 맞는 `</Modal>`까지 잘라 낸다.
 * 중첩된 Modal이 있으므로 여는 태그와 닫는 태그의 깊이로 짝을 찾는다.
 */
function modalBlock(src, start) {
  const tag = /<(\/?)Modal\b/g;
  tag.lastIndex = start;
  let depth = 0;
  for (let m = tag.exec(src); m; m = tag.exec(src)) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) return src.slice(start, tag.lastIndex);
  }
  return src.slice(start, start + 3000);
}

/** 버튼 라벨·제목·주석을 뺀 본문을 돌려준다. 결과 설명은 이 본문에 있어야 한다. */
function bodyOf(block) {
  return block
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, " ")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/<Button[\s\S]*?<\/Button>/g, " ")
    .replace(/<[A-Z]\w*Button[\s\S]*?\/>/g, " ")
    .replace(/title=(?:"[^"]*"|\{[^}]*\})/g, " ");
}

const bad = [];
let total = 0;
for (const file of walk(SRC)) {
  const rel = file.slice(SRC.length + 1);
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(/<Modal\b/g)) {
    const block = modalBlock(src, m.index);
    const head = block.slice(0, 400);
    const tm = head.match(/title=(?:"([^"]*)"|\{([^}]{0,50}))/);
    const title = tm?.[1] ?? "{동적}";
    if (!(block.includes('variant="danger"') || DANGER.test(title))) continue;
    total += 1;
    if (EFFECT.test(bodyOf(block))) continue;
    bad.push({ rel, title, key: `${rel} :: ${title}` });
  }
}

const unexpected = bad.filter((b) => !(b.key in EXPECTED));
const stale = Object.keys(EXPECTED).filter(
  (k) => !bad.some((b) => b.key === k),
);

if (unexpected.length) {
  console.error(
    `\n❌ 무슨 일이 일어나는지 안 말하는 확인창 ${unexpected.length}건\n`,
  );
  for (const b of unexpected) console.error(`   ${b.rel}  「${b.title}」`);
  console.error(
    "\n   누르면 무엇이 사라지고 무엇이 남는지, 되돌릴 수 있는지를 같은 화면에 쓴다.",
  );
  console.error(
    "   ⚠ 서버가 실제로 무엇을 하는지 **코드를 열어 보고** 쓴다 — 이 검사는 문장이",
  );
  console.error("      있는지만 보지 그 말이 참인지는 못 본다.");
  console.error("   정말 말할 것이 없으면 EXPECTED 에 이유와 함께 적는다.");
} else {
  console.log(`✅ 위험 확인창 ${total}개 전부 무슨 일이 일어나는지 말한다`);
}
if (stale.length) {
  console.log(
    `\n⚠️  EXPECTED 에 있는데 더는 해당 없는 것 ${stale.length}건 — 지워도 된다`,
  );
  for (const k of stale) console.log(`   ${k}`);
}

process.exit(process.argv.includes("--strict") && unexpected.length ? 1 : 0);
