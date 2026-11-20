/**
 * 화면이 부르는데 서버에 없는 라우트를 센다. `check-orphan-routes`의 반대 방향 검사다.
 *
 * 화면이 약속하는 동작 전체는 실화면을 봐야 알 수 있다. 경로 수준은 기계로 잴 수 있다.
 * 화면이 서버에 없는 경로를 부르면 반드시 404가 난다.
 * 그 화면은 대개 "데이터 없음"처럼 알림 없이 오작동한다.
 *
 * 응답 형태(필드 이름·타입)가 다른 것은 여기서 보지 않는다. 그 문제는 `make contracts`가
 * 만드는 타입과 `tsc`가 잡는다.
 *
 * 비교 방법: 화면은 경로를 템플릿 리터럴로 쓴다(`api(\`/projects/${pid}/clips/${id}\`)`).
 * 그 경로를 뽑아 `${…}`를 `{}`로 정규화하고, OpenAPI 경로도 `{param}`을 `{}`로 정규화해
 * 맞춘다. 이름이 달라도(`pid`와 `project_id`) 위치가 같으면 같은 경로다.
 *
 * 쿼리스트링(`?`)과 그 뒤는 잘라 낸다. 경로 자체가 변수인 호출(`${base}?${u}` 같은 것)은
 * 셀 수 없어 건너뛴다. 센 것처럼 보고하지 않고, 건너뛴 호출을 따로 출력한다.
 *
 * 이 유형의 결함은 생겨도 드러나지 않으므로 결함이 없어도 게이트를 둔다.
 * 라우트를 지우거나 이름을 바꿀 때 이 검사가 먼저 실패한다.
 *
 * 사용법:  node scripts/ci/check-missing-routes.mjs
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const FE = new URL("../../apps/web/src", import.meta.url).pathname;
const SPEC = new URL("../../docs/api/openapi.json", import.meta.url).pathname;

/**
 * 측정할 수 없는 호출을 가린다. 셀 수 없는 호출을 센 것처럼 보고하지 않는다.
 *
 * 1. 경로가 변수로 시작하거나 슬래시로 시작하지 않는 것(`${base}?${u}`).
 * 2. 중첩 템플릿 리터럴에서 잘린 것. 호출이 `` `/a/${x ? `?q=1` : ""}` `` 처럼
 *    안쪽에 백틱을 또 쓰면 바깥 백틱의 짝 찾기가 안쪽에서 멈춘다. 그 조각을 그대로
 *    비교하면 정상 경로가 없는 경로로 잡힌다.
 *    닫히지 않은 `${`가 남아 있으면 잘린 것으로 본다.
 */
const truncated = (raw) => {
  let depth = 0;
  for (let i = 0; i < raw.length; i++) {
    if (raw[i] === "$" && raw[i + 1] === "{") { depth++; i++; }
    else if (raw[i] === "}") depth = Math.max(0, depth - 1);
  }
  return depth > 0;
};
const UNMEASURABLE = /^\$\{|^[^/]/;

/** 디렉터리를 재귀로 돌며 `.ts`/`.tsx` 파일 경로를 모은다. */
function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(p) ? [p] : [];
  });
}

/**
 * 경로를 정규화한다. 경로 변수는 이름을 지우고 위치만 남기며, 쿼리와 끝 슬래시를 뗀다.
 *
 * 화면의 `${pid}`와 명세의 `{project_id}`를 둘 다 `{}`로 바꾼다. 이름이 달라도
 * 위치가 같으면 같은 경로다.
 *
 * 주의: 두 형태를 정규식 하나로 한 번에 바꾼다. 치환을 두 번으로 나누면 첫 치환이 닫는
 * 중괄호를 없애 둘째 치환이 맞지 않는다. 그러면 `/projects/{/grievances` 같은 결과가 나와
 * 정상 경로가 모두 "없는 경로"로 잡힌다.
 */
const norm = (p) =>
  p.replace(/\$\{[^}]*\}|\{[^}]*\}/g, "{}").split("?")[0].replace(/\/$/, "");

const spec = JSON.parse(readFileSync(SPEC, "utf8"));
const serverPaths = new Set(Object.keys(spec.paths).map(norm));

const found = new Map();   // 키는 정규화 경로, 값은 [원본 예시, 파일:줄]
const skipped = [];
for (const file of walk(FE)) {
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/\bapi(?:<[^>]*>)?\(\s*`([^`]+)`/g)) {
      const raw = m[1];
      if (UNMEASURABLE.test(raw) || truncated(raw)) {
        skipped.push(`${raw}  (${file.replace(FE, "src")}:${i + 1})`);
        continue;
      }
      const n = norm(raw);
      if (!found.has(n)) found.set(n, [raw, `${file.replace(FE, "src")}:${i + 1}`]);
    }
  });
}

const missing = [...found].filter(([n]) => !serverPaths.has(n));

if (missing.length === 0) {
  console.log(`✅ 화면이 부르는 경로 ${found.size}종 — 서버에 다 있다`);
} else {
  console.log(`⚠️  화면이 부르는데 **서버에 없는 경로 ${missing.length}종**:\n`);
  for (const [n, [raw, where]] of missing) console.log(`  ${where}\n      호출: ${raw}\n      정규화: ${n}`);
  console.log("\n404가 예약된 자리다. 오타인지, 서버가 안 만든 것인지, 프록시가 접두를 붙이는지 본다.");
}
if (skipped.length) {
  console.log(`\n(경로가 변수라 못 재는 호출 ${skipped.length}건 — 손으로 본다)`);
  for (const s of [...new Set(skipped)].slice(0, 5)) console.log(`  ${s}`);
}
process.exit(missing.length ? 1 : 0);
