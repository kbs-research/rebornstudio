/**
 * 웹 소스가 도메인 경계(`features/<도메인>/`) 규칙을 얼마나 어기는지 센다.
 *
 * 규칙은 네 가지다.
 * 1. `api()`는 `features/<도메인>/api.ts`와 `shared/api/`에서만 부른다. 화면이 경로 문자열을 직접 가지면
 *    같은 요청이 화면마다 다른 모양으로 흩어지고, 서버 경로가 바뀔 때 고칠 곳을 셀 수 없다.
 * 2. 쿼리 키는 도메인의 키 팩토리(`projectKeys.list()` 모양)로만 만든다. 배열을 손으로 적으면
 *    무효화(`invalidateQueries`)가 키 하나를 놓쳐도 오류 없이 이전 데이터가 화면에 남는다.
 * 3. 다른 feature는 그 feature의 `index.ts`로만 import한다. 속 파일을 직접 부르면 옮기거나 쪼갤 때
 *    바깥이 같이 깨진다.
 * 4. `shared/`는 `features/`를 import하지 않는다. 공용 부품이 도메인을 알면 도메인을 옮길 때마다
 *    공용 부품이 같이 흔들린다. 같은 이유로 `shared/`가 `shared/` 밖을 읽는 수도 함께 센다.
 *
 * 규칙 밖으로 하나를 더 센다. 기능 폴더로 옮기기 전 경로(`@/components/ui/*`·`@/lib/api` 등)로 import한 수다.
 * 지금은 그 경로에 파일이 없어 `tsc`와 빌드가 먼저 막지만, 이전 경로에 같은 이름의 파일이 다시 생기면
 * `tsc`가 오류 없이 그 파일을 읽는다. 그래서 이 표가 경로 자체를 막는다.
 *
 * 옵션 없이 부르면 위반 수만 찍고 늘 0으로 끝난다. CI는 `--strict`로 불러 한 건이라도 있으면 1로 끝낸다.
 * `--list`는 규칙마다 파일별 수를 찍는다.
 *
 * 주의: 주석에 예시로 적은 `api(`나 `queryKey: [`를 세지 않도록 주석은 지우고 센다.
 * 문자열 속 `//`(주소)는 앞이 `:`이면 주석에서 뺀다.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;
const LIST = process.argv.includes("--list");
const STRICT = process.argv.includes("--strict");

/** 디렉터리를 재귀로 돌며 `.ts`·`.tsx` 파일 경로를 모은다. */
function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(e)) out.push(p);
  }
  return out;
}

/** 블록 주석과 줄 주석을 지운다. 줄 수는 그대로 둔다. */
const stripComments = (src) =>
  src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

/** import·export 지정자를 모두 뽑는다. */
const specifiers = (src) => [...src.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)].map((m) => m[1]);

/** 기능 폴더로 옮기기 전 경로. 지금 위치는 `shared/`·`shell/`·`app/`·`features/<도메인>/`이다. */
const LEGACY = [
  /^@\/components\/ui\//,
  /^@\/components\/shell\//,
  /^@\/components\/(RoleGuard|RoleLink)$/,
  /^@\/lib\/(cn|time|josa|fmtSecs|fmtBytes|ymd|diff|countdown|download|api|auth|error_text|queryClient)$/,
  /^@\/routes\/(router|_layout|redirects)$/,
  /^@\/hooks\/(useOutsideClose|useDialogFocus|useDocumentTitle|useMediaQuery|useUnsavedGuard)$/,
  /^@\/shared\/ui\/(AccessDenied|RouteError|PreviewModal|StatusBadge)$/,
];

/** `api`를 API 클라이언트에서 가져오면 true. 가져오지 않은 파일의 `api(`는 다른 함수다. */
const importsApi = (src) =>
  /import\s*\{[^}]*\bapi\b[^}]*\}\s*from\s*["'](@\/shared\/api\/client|@\/lib\/api|\.\/client)["']/.test(src);

/** 파일이 속한 feature 이름을 돌려준다. `features/` 밖이면 `null`. */
const featureOf = (rel) => rel.match(/^features\/([^/]+)\//)?.[1] ?? null;

const rules = {
  api: { title: "api()를 features/*/api.ts·shared/api 밖에서 부름", hits: new Map() },
  queryKey: { title: "쿼리 키 배열을 키 팩토리 밖에서 만듦", hits: new Map() },
  crossFeature: { title: "다른 feature를 index.ts 밖으로 import", hits: new Map() },
  sharedToFeatures: { title: "shared/가 features/를 import", hits: new Map() },
  sharedOutside: { title: "shared/가 shared/ 밖을 import", hits: new Map() },
  legacy: { title: "옮기기 전 경로로 import", hits: new Map() },
};

/** 규칙 `key`에 파일 `rel`의 위반 `n`건을 더한다. */
function add(key, rel, n) {
  if (!n) return;
  const m = rules[key].hits;
  m.set(rel, (m.get(rel) ?? 0) + n);
}

const files = walk(SRC);
for (const file of files) {
  const rel = relative(SRC, file);
  const src = stripComments(readFileSync(file, "utf8"));
  const specs = specifiers(src);
  const feature = featureOf(rel);
  const inShared = rel.startsWith("shared/");
  const isFeatureApi = /^features\/[^/]+\/api\.ts$/.test(rel);

  if (importsApi(src) && !isFeatureApi && !rel.startsWith("shared/api/")) {
    add("api", rel, (src.match(/\bapi\s*(<|\()/g) ?? []).length);
  }
  if (!isFeatureApi) add("queryKey", rel, (src.match(/queryKey:\s*\[/g) ?? []).length);

  for (const s of specs) {
    if (LEGACY.some((re) => re.test(s))) add("legacy", rel, 1);
    const target = s.match(/^@\/features\/([^/]+)(\/.*)?$/);
    if (target && feature && target[1] !== feature && target[2] && target[2] !== "/index") add("crossFeature", rel, 1);
    if (inShared && target) add("sharedToFeatures", rel, 1);
    if (inShared && s.startsWith("@/") && !s.startsWith("@/shared/")) add("sharedOutside", rel, 1);
  }
}

/** 규칙 하나의 위반 곳 수와 파일 수를 돌려준다. */
const tally = (r) => [[...r.hits.values()].reduce((a, b) => a + b, 0), r.hits.size];

console.log(`feature 경계 (${STRICT ? "막는 모드" : "보고 모드, 늘 통과"}) · 검사 파일 ${files.length}개`);
let total = 0;
for (const [i, r] of Object.values(rules).entries()) {
  const [n, f] = tally(r);
  total += n;
  console.log(`  ${i + 1}. ${String(n).padStart(4)}곳 · ${String(f).padStart(3)}파일  ${r.title}`);
  if (LIST && f) {
    for (const [rel, k] of [...r.hits].sort((a, b) => b[1] - a[1])) console.log(`        ${String(k).padStart(4)}  src/${rel}`);
  }
}
if (!LIST && total) console.log("  → 자리는 --list 로 봅니다.");
process.exit(STRICT && total ? 1 : 0);
