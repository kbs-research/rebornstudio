/**
 * 입력란에 접근 가능한 이름이 있는지 검사한다.
 *
 * `placeholder`는 글자를 입력하기 시작하면 사라지고, 보조기술이 이름으로 읽을지도 정해져 있지 않다.
 * 이름이 없는 입력란은 "편집 텍스트"로만 읽혀서 무엇을 넣는 입력란인지 알 수 없다.
 * 이름 없는 입력도 정상적으로 컴파일되므로 `tsc`와 `vite build`는 이 문제를 잡아내지 못한다.
 *
 * 검사 대상은 `placeholder`가 있는 `<input>`·`<textarea>`·`<Input>`·`<Textarea>` 중 이름이 없는 요소다.
 * 이름으로 보는 속성은 `aria-label`, `aria-labelledby`, `id`다.
 * `<label htmlFor>`는 다른 파일이나 다른 컴포넌트에 있을 수 있어 이 스크립트가 확인할 수 없으므로, `id`가 있으면 통과시킨다.
 * 오탐이 잦은 게이트는 곧 무시되므로 판정은 놓치는 쪽으로 기울게 둔다.
 *
 * 이 검사의 한계는 두 가지다.
 * 1. 아이콘만 있는 버튼은 검사하지 않는다. `<button><X/></button>`도 이름이 없지만, JSX만 보고
 *    글자가 없는지 가리면 오탐이 많다. 이런 버튼은 화면에서 확인한다.
 * 2. 이름이 있는지만 확인하고 이름이 맞는지는 확인하지 않는다. `aria-label="입력"`도 통과한다.
 *
 * 두 번째 검사는 이름이 서로 구별되는지 확인한다.
 * `.map(...)` 콜백 안의 고정 문자열 `aria-label`을 센다. 반복해서 그리는 요소에 고정 이름을
 * 쓰면 행마다 같은 이름이 되어, 문장 버튼 여러 개가 모두 `문장 수정`으로 읽힌다.
 *
 * 두 번째 검사는 `--strict`에서도 실패로 치지 않고 건수만 보여 준다. 이름이 이미 구별되는 경우가
 * 섞여 있어 화면에서 확인해야 판정할 수 있다. 확인하지 않은 항목을 실패로 만들면 이유 없이
 * `EXPECTED`에 넣게 된다. 항목은 화면에서 확인해 고치거나 이유를 적는다(`check-orphan-routes`와 같은 방식).
 *
 * 사용법:  node scripts/check-a11y-names.mjs
 *          node scripts/check-a11y-names.mjs --strict   (목록에 없는 항목이 있으면 exit 1, CI용)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

/**
 * 이름 없이 두어도 되는 입력 목록. 키는 `파일 :: placeholder` 형식이고 값에 이유를 적는다.
 *
 * 항목을 넣을 때는 화면을 열어 확인한 뒤 적는다.
 */
const EXPECTED = {};

/** 이름으로 인정하는 속성. `id`는 다른 곳의 `<label htmlFor>`를 확인할 수 없어 통과로 둔다. */
const NAMED = /\baria-label[=\s]|\baria-labelledby[=\s]|\bid=/;

/**
 * 입력이 `<label>` 안에 있으면 true. 이름이 있는 것으로 본다.
 * 예: `<label><span>제목</span><Input/></label>`.
 *
 * 이 저장소에는 두 가지 작성 방식이 섞여 있다. 감싼 방식은 브라우저가 이름을 연결하고,
 * 옆에 둔 방식(`<label>제목</label><Input/>`)은 `htmlFor`가 있어야 연결된다.
 * 감싼 방식까지 실패로 세면 오탐이 많아져 이 검사가 곧 무시된다.
 *
 * `at` 앞의 마지막 여는 `<label`이 마지막 닫는 `</label>`보다 뒤에 있으면 아직 label 안이다.
 */
function insideLabel(src, at) {
  return src.lastIndexOf("<label", at) > src.lastIndexOf("</label>", at);
}

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
 * `<input …>` 여는 태그 하나를 잘라 낸다.
 * 문자열 안의 `>`를 태그 끝으로 보지 않도록 따옴표 안과 중괄호 안은 건너뛴다.
 */
function openTag(src, start) {
  let depth = 0;
  for (let i = start; i < src.length; i += 1) {
    const c = src[i];
    if (c === '"' || c === "'") {
      const q = c;
      i += 1;
      while (i < src.length && src[i] !== q) i += 1;
    } else if (c === "{") depth += 1;
    else if (c === "}") depth -= 1;
    else if (c === ">" && depth === 0) return src.slice(start, i + 1);
  }
  return src.slice(start, start + 1200);
}

const bad = [];
let total = 0;
for (const file of walk(SRC)) {
  const rel = file.slice(SRC.length + 1);
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(/<(input|textarea|Input|Textarea)\b/g)) {
    const tag = openTag(src, m.index);
    if (!/\bplaceholder[=\s]/.test(tag)) continue;
    total += 1;
    if (NAMED.test(tag) || insideLabel(src, m.index)) continue;
    const ph =
      tag.match(/placeholder=(?:"([^"]*)"|\{[^}]{0,40})/)?.[1] ?? "{동적}";
    // 받는 사람이 다시 찾지 않도록 파일 이름과 함께 줄 번호를 출력한다.
    const line = src.slice(0, m.index).split("\n").length;
    bad.push({ rel, ph, line, key: `${rel} :: ${ph}` });
  }
}

const unexpected = bad.filter((b) => !(b.key in EXPECTED));
const stale = Object.keys(EXPECTED).filter(
  (k) => !bad.some((b) => b.key === k),
);

if (unexpected.length) {
  console.error(
    `\n❌ placeholder 만 있고 이름이 없는 입력 ${unexpected.length}건\n`,
  );
  for (const b of unexpected)
    console.error(`   ${b.rel}:${b.line}  「${b.ph}」`);
  console.error(
    "\n   `aria-label`을 준다 — placeholder 와 같은 말이어도 된다(치는 동안 사라지지 않는다).",
  );
  console.error("   `<label>`이 붙어 있으면 `id`로 이어 준다.");
} else {
  console.log(`✅ placeholder 있는 입력 ${total}개 전부 이름이 있다`);
}
if (stale.length) {
  console.log(
    `\n⚠️  EXPECTED 에 있는데 더는 해당 없는 것 ${stale.length}건 — 지워도 된다`,
  );
  for (const k of stale) console.log(`   ${k}`);
}


/*
  두 번째 검사: 반복해 그리는 요소의 고정 이름. `.map(` 콜백 안에서 `aria-label="…"`(고정 문자열)을 쓰면 행마다 같은 이름이 된다.
  이 검사는 놓치는 쪽으로 기울어도 되므로 파서 없이 괄호·중괄호 깊이로 콜백 범위를 대략 잡는다.
  템플릿 리터럴(`{\`문장 ${i + 1} 수정\`}`)은 고친 형태라서 잡지 않는다.
*/
const REPEATED = [];
for (const f of walk(SRC)) {
  const lines = readFileSync(f, "utf8").split("\n");
  let depth = 0;
  let mapDepth = null;
  lines.forEach((ln, i) => {
    if (mapDepth === null && /\.map\(\s*\(?[^)]*\)?\s*=>/.test(ln)) mapDepth = depth;
    for (const ch of ln) {
      if (ch === "(" || ch === "{") depth++;
      if (ch === ")" || ch === "}") depth--;
    }
    if (mapDepth !== null && depth <= mapDepth) mapDepth = null;
    const m = ln.match(/aria-label="([^"]+)"/);
    if (m && mapDepth !== null) {
      REPEATED.push({ rel: f.replace(SRC, ""), line: i + 1, name: m[1] });
    }
  });
}
if (REPEATED.length) {
  console.log(
    `\n⚠️  되풀이되는 자리에 고정 이름 ${REPEATED.length}건 — 행마다 같은 이름으로 읽힌다`,
  );
  for (const r of REPEATED) console.log(`   ${r.rel}:${r.line}  「${r.name}」`);
  console.log(
    "\n   무엇의 것인지를 이름에 넣는다 — 화면에 번호가 보이면 그 번호를(`문장 3 삭제`).",
  );
  console.log("   정말 하나뿐이거나 이름이 이미 구별되면 그대로 둔다 — 세는 것이지 막는 것이 아니다.");
} else {
  console.log("✅ 되풀이되는 자리에 고정 이름 없음");
}

process.exit(process.argv.includes("--strict") && unexpected.length ? 1 : 0);
