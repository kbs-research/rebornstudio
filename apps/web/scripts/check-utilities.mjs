/**
 * 소스가 쓰는 Tailwind 유틸이 빌드 CSS에 실제로 생성되는지 검사한다.
 *
 * 빌드 CSS에 없는 유틸은 오류 없이 무시된다. `tsc`와 `vite build`는 이를 잡아내지 못하고, 클래스 이름이
 * 그럴듯해서 리뷰에서도 눈에 띄지 않는다.
 * 예: `@theme`에 등록하지 않은 토큰(`bg-ws-stage`)은 투명해지고, 표준 이름이 아닌
 * `text-danger`는 본문과 같은 검정이 되고, 타입 스케일에 없는 `text-title-3`은 본문 크기가 된다.
 *
 * 검사 범위는 시맨틱 토큰을 쓰는 접두사(`bg-`·`text-`·`border-`…)로 좁힌다.
 * Tailwind 기본 유틸(`flex`·`gap-2`)까지 보면 잡음이 커지고, 오타는 대개 토큰 쪽에서 난다.
 *
 * 두 번째 검사는 타입 스케일 밖 유틸을 막는다.
 * 첫 번째 검사는 CSS에 있는지만 확인하는데, Tailwind 기본 크기 유틸(`text-sm`·`text-lg`…)은
 * CSS에는 있지만 이 저장소의 타입 스케일 밖이다. 루트가 14px이라 `text-sm`은 12.25px,
 * `text-lg`는 15.75px가 되어 스케일(10·11·12·13·14·16·20·24·32)에 없는 소수 픽셀이 된다.
 *
 * 세 번째 검사는 같은 이유로 표준 라운드 밖 유틸을 막는다.
 * `rounded-md`·`rounded-2xl`은 CSS에 있지만 이 저장소는 라운드를 이름(`field` 7, `control` 7, `card` 10,
 * `overlay` 12, `bubble` 14, `capsule` 9999)으로 고른다. px 값으로 고르면 Tailwind 기본값이 바뀔 때 모양이 알림 없이 바뀐다.
 *
 * 주석이 유틸을 설명하느라 적은 이름은 쓰인 클래스가 아니므로 소스는 주석을 뺀 코드만 검사한다.
 * 이전 dist와 대조하면 새 유틸이 없다고 나오므로, 빌드 CSS가 소스보다 오래됐으면 먼저 경고한다.
 *
 * 사용법:  node scripts/check-utilities.mjs   (`dist/`가 있어야 한다. 먼저 vite build를 실행한다)
 * `dist/assets`와 `src`를 현재 디렉터리 기준으로 읽으므로 `apps/web`에서 실행한다.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";
import ts from "typescript";

// 검사할 유틸의 접두사. 검사를 추가할 때는 그 접두사가 이 목록에 있는지부터 확인한다.
// 접두사가 빠져 있으면 유틸 수집 자체가 되지 않아 새 검사가 아무것도 잡지 못한다.
const PREFIX = ["bg-", "text-", "border-", "ring-", "shadow-", "fill-", "stroke-", "divide-", "outline-", "placeholder-", "rounded-"];

const cssDir = "dist/assets";
const cssFile = readdirSync(cssDir).find((f) => f.endsWith(".css"));
if (!cssFile) {
  console.error("dist/assets 에 CSS 가 없다 — `npx vite build` 를 먼저 돌린다.");
  process.exit(2);
}
const css = readFileSync(join(cssDir, cssFile), "utf8");

const files = execSync('find src -name "*.tsx" -o -name "*.ts"', { encoding: "utf8" }).trim().split("\n");

// 빌드 CSS가 소스보다 오래됐으면 알린다. 이전 dist와 대조하면 새로 쓴 유틸이 없다고 잘못 나온다.
const cssTime = statSync(join(cssDir, cssFile)).mtimeMs;
const newest = files.reduce((a, f) => Math.max(a, statSync(f).mtimeMs), 0);
if (newest > cssTime) {
  console.error("⚠ dist/assets의 CSS가 소스보다 오래됐다. `npx vite build`를 먼저 돌려야 결과를 믿을 수 있다.\n");
}

/**
 * 주석을 뺀 코드를 돌려준다. 주석이 유틸 이름을 설명하느라 적은 백틱(`rounded-md`)을 클래스로 읽지 않게 한다.
 * 문자열 리터럴과 JSX는 그대로 남는다.
 */
function withoutComments(text, fileName) {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, false, kind);
  return ts.createPrinter({ removeComments: true }).printFile(sf);
}

const used = new Map();
for (const f of files) {
  const src = withoutComments(readFileSync(f, "utf8"), f);
  // 모든 문자열 리터럴을 본다. `className=`만 보면 상수 안의 클래스를 놓친다
  // (`const TONE = { fail: { cls: "text-danger" } }`).
  for (const m of src.matchAll(/["'`]([^"'`\n]{2,240})["'`]/g)) {
    for (const raw of m[1].split(/\s+/)) {
      const tok = raw.replace(/^!/, "");
      // variant(`hover:`·`last:`)는 CSS 선택자에 그대로 남으므로(`.hover\:border-label-3:hover`) 떼지 않는다.
      // 떼고 찾으면 모두 "없음"으로 나온다.
      const base = tok.split(":").pop();
      if (!PREFIX.some((p) => base.startsWith(p))) continue;
      if (/[[\]${}(),;]/.test(tok)) continue;      // 임의값·템플릿·문장 조각은 건너뛴다.
      if (!/^[a-z][a-z0-9:/.-]*$/.test(tok)) continue;
      if (!used.has(tok)) used.set(tok, new Set());
      used.get(tok).add(f);
    }
  }
}

// Tailwind는 선택자에서 `/`·`.`·`:`를 이스케이프한다. variant를 포함한 전체 토큰으로 찾는다.
const esc = (t) => t.replace(/[/.:]/g, (c) => "\\" + c);
/** 토큰이 빌드 CSS에 선택자로 있으면 true를 돌려준다. */
const present = (t) => css.includes(`.${esc(t)}`) || css.includes(`.${t}`);
const missing = [...used].filter(([t]) => !present(t));

// ── 타입 스케일 밖 유틸 ─────────────────────────────────────────────────────
// `text-<숫자>` 같은 패턴으로 잡으면 `text-title-2`가 걸리므로 이름을 나열한다.
const OFF_SCALE = [
  // 타입 스케일: nano10 micro11 caption12 footnote13 callout14 body14 headline16 title-2 20 title-1 24 display32
  "text-xs", "text-sm", "text-base", "text-lg", "text-xl", "text-2xl", "text-3xl", "text-4xl",
  // 표준 라운드: field7 control7 card10 overlay12 bubble14 capsule9999
  "rounded-sm", "rounded-md", "rounded-lg", "rounded-xl",
  "rounded-2xl", "rounded-3xl", "rounded-full", "rounded-none",
];
const offScale = [...used].filter(([t]) => {
  const base = t.split(":").pop();
  // 방향 변형(`rounded-tr-md`)도 같은 문제다. 방향 접두사와 크기 꼬리로 잡는다.
  return OFF_SCALE.includes(base) || OFF_SCALE.some((n) => {
    const [head, tail] = [n.slice(0, n.lastIndexOf("-")), n.slice(n.lastIndexOf("-") + 1)];
    return head === "rounded" && /^rounded-(t|b|l|r|tl|tr|bl|br|s|e|ss|se|es|ee)-/.test(base)
      && base.endsWith(`-${tail}`);
  });
});

if (missing.length === 0 && offScale.length === 0) {
  console.log(`✅ 유틸 ${used.size}종 — 전부 빌드 CSS 에 있고 타입·라운드 정본 안이다`);
  process.exit(0);
}
if (offScale.length > 0) {
  console.error(`❌ 정본 밖 유틸 ${offScale.length}종 — 이름이 아니라 값으로 고르고 있다:\n`);
  for (const [t, fs] of offScale.sort()) console.error(`  ${t.padEnd(26)} ${[...fs].join(", ")}`);
  console.error("\n타입:   nano10 · micro11 · caption12 · footnote13 · callout14 · body14 · headline16 · title-2 20 · title-1 24 · display32");
  console.error("라운드: field7 · control7 · card10 · overlay12 · bubble14 · capsule9999");
  if (missing.length === 0) process.exit(1);
  console.error("");
}
console.error(`❌ 빌드 CSS 에 없는 유틸 ${missing.length}종 — 조용히 무시되고 있다:\n`);
for (const [t, fs] of missing.sort()) console.error(`  ${t.padEnd(26)} ${[...fs].join(", ")}`);
console.error("\n토큰 이름이 맞는지, `@theme` 에 올라와 있는지 본다.");
process.exit(1);
