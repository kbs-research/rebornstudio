/**
 * 화면이 본문 없이 보내는 변경 요청 중 서버가 본문을 요구하는 것을 찾는다.
 *
 * 기계로 세는 이유: 이 불일치는 타입 검사에 걸리지 않는다. `api()`의 옵션은 `RequestInit`이라
 * body가 없어도 컴파일된다. 서버는 요청을 받은 뒤에야 422를 낸다. 화면에는 "입력값이 올바르지
 * 않습니다"만 뜬다. 입력란이 없는 화면이라 사람이 원인을 찾기 어렵다.
 *
 * 판정: `docs/api/openapi.json`에서 `requestBody.required`인 경로를 모으고,
 * `apps/web/src`의 `api<…>(`…`, { method: "POST|PUT|PATCH" … })` 호출 중 `body`가 없는
 * 것과 맞춘다. 경로 변수(`${id}` · `{user_id}`)는 양쪽 모두 `{}`로 바꿔 비교한다.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../..", import.meta.url).pathname;
const spec = JSON.parse(readFileSync(join(ROOT, "docs/api/openapi.json"), "utf8"));
const norm = (p) => p.replace(/\$\{[^}]+\}/g, "{}").split("?")[0].replace(/\{[^}]*\}/g, "{}");

const needsBody = new Map();
for (const [path, ops] of Object.entries(spec.paths)) {
  for (const [method, op] of Object.entries(ops)) {
    if (!["post", "put", "patch"].includes(method)) continue;
    if (op.requestBody?.required) needsBody.set(`${method.toUpperCase()} ${norm(path)}`, path);
  }
}

const files = [];
/** `apps/web/src` 아래의 `.ts`/`.tsx` 파일을 재귀로 돌며 `files`에 모은다. */
(function walk(dir) {
  for (const e of readdirSync(dir)) {
    const f = join(dir, e);
    if (statSync(f).isDirectory()) walk(f);
    else if (/\.tsx?$/.test(f)) files.push(f);
  }
})(join(ROOT, "apps/web/src"));

const hits = [];
for (const f of files) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/api<[^>]*>\(\s*`([^`]+)`\s*,\s*\{([^}]*)\}/g)) {
    const [, path, opts] = m;
    const method = (opts.match(/method:\s*"(\w+)"/) ?? [, "GET"])[1];
    if (!["POST", "PUT", "PATCH"].includes(method)) continue;
    if (/\bbody\s*:/.test(opts)) continue;
    const key = `${method} ${norm(path)}`;
    if (needsBody.has(key)) {
      const line = src.slice(0, m.index).split("\n").length;
      hits.push(`${f.replace(ROOT, "")}:${line}  ${method} ${path}  ← 서버 스펙 ${needsBody.get(key)} 는 본문이 필수입니다`);
    }
  }
}

if (hits.length === 0) {
  console.log(`✅ 본문 없는 변경 요청 ${0}건 — 서버가 요구하는 본문을 모두 보냅니다`);
  process.exit(0);
}
console.error(`❌ 본문이 빠진 요청 ${hits.length}건`);
for (const h of hits) console.error(`   ${h}`);
console.error("\n   고치는 법: 고를 것이 없으면 `body: JSON.stringify({})`, 값이 있으면 그 값을 담습니다.");
process.exit(1);
