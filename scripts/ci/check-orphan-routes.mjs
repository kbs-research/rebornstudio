/**
 * BE 라우트 중 화면이 한 번도 부르지 않는 것을 센다.
 *
 * 화면과 서버가 어긋나는 방향은 둘이다. 화면이 약속하고 서버가 지키지 않는 경우는
 * 실화면을 봐야 알 수 있다. 서버에 라우트·서비스·계약·테스트가 모두 있는데 화면이 부르지
 * 않는 경우는 기계가 셀 수 있다. `openapi.json`의 경로와 `apps/web/src`를 대조하면 된다.
 *
 * 대조 방법: 화면은 URL을 템플릿 리터럴로 만든다(`/projects/${pid}/members`). 그래서 경로
 * 전체가 아니라 마지막 `{param}` 뒤의 리터럴 꼬리로 찾는다. 그 조각은 소스에 그대로 있다.
 *   /projects/{project_id}/members            → "/members"
 *   /work/{work_item_id}/submit               → "/submit"
 *   /healthz                                  → "/healthz"
 *
 * 꼬리가 짧으면(`/role`) 다른 문자열에도 걸려 누락은 생길 수 있다. 오탐은 나지 않는다.
 * 그래서 보고된 라우트는 실제로 부르는 곳이 없다. 이 검사는 그 방향으로만 쓴다.
 *
 * 화면이 부르지 않는 것이 정상인 라우트도 있다. 인프라 프로브, 웹훅,
 * 브라우저가 직접 여는 다운로드는 fetch로 부를 일이 없다. 이런 라우트는 `EXPECTED`에 이유와
 * 함께 적는다. 목록에 이유가 없으면 다음 사람이 지워도 되는지 알 수 없다.
 *
 * 사용법:  node scripts/ci/check-orphan-routes.mjs
 *          node scripts/ci/check-orphan-routes.mjs --strict   (하나라도 있으면 exit 1)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../..", import.meta.url).pathname;
const SPEC = join(ROOT, "docs/api/openapi.json");
const FE = join(ROOT, "apps/web/src");

/** 화면이 부르지 않는 것이 정상인 라우트 목록. 경로별로 이유를 적는다. */
const EXPECTED = {
  "/healthz": "인프라 프로브 (k8s liveness)",
  "/readyz": "인프라 프로브 (k8s readiness)",
  // 운영 web nginx의 CSP가 report-uri·report-to로 이 경로를 가리킨다. 화면 코드가 아니라 브라우저가 위반을 보낸다.
  "/csp-report": "브라우저가 CSP 위반을 보고하는 수집처 (인증 없음)",
  "/exports/{export_id}/download": "브라우저가 직접 연다 (파일 다운로드)",

  // ── 화면이 아니라 다른 곳이 쓰는 BE 라우트 ──────────────────────────────────
  // 테스트가 사용하는 라우트도 쓰이는 코드다. 아래는 실제로 확인한 것만 이유와 함께
  // 남긴다. grep만 보고 제거 대상을 정하지 않고 테스트도 확인한다.
};

/** 디렉터리를 재귀로 돌며 `.ts`/`.tsx` 파일 경로를 모은다. */
function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(p) ? [p] : [];
  });
}

/** 마지막 `{param}` 뒤의 리터럴 꼬리를 돌려준다. 파라미터로 끝나면 null이다. */
function tail(path) {
  const segs = path.replace(/^\//, "").split("/");
  let lastParam = -1;
  segs.forEach((s, i) => { if (s.startsWith("{")) lastParam = i; });
  const rest = segs.slice(lastParam + 1);
  return rest.length ? "/" + rest.join("/") : null;
}

/**
 * 파라미터로 끝나는 경로를 찾는 정규식을 만든다.
 *
 * 화면은 이런 경로를 템플릿 리터럴로 만든다(`/projects/${pid}/clips/${clipId}`).
 * 그래서 경로를 그대로 정규식으로 바꾼다. 리터럴은 escape하고 `{param}`은 `${…}`로 바꾸며,
 * 끝의 파라미터는 여는 `${`까지만 본다.
 * 이런 경로를 `못 잰다`로 넘기면 아무도 손으로 확인하지 않아 누락이 드러나지 않는다.
 *
 * 주의: 마지막 조각만 보지 않고 경로 전체로 좁힌다.
 * `clips/${`만 찾으면 다른 라우트의 호출(`/clips/${id}/rights/recover`)이 그 조건을 대신
 * 만족시킨다. 그러면 호출을 지워도 통과한다.
 *
 * 남은 한계 둘은 알고 쓴다.
 * 1. 짧은 경로는 긴 경로의 호출로도 만족된다. `/work/{id}`는 `/work/${x}/release` 호출로도
 *    참이 된다. 접두가 같으면 구분할 수 없다. 이 한계는 과소 보고(누락)라서 오탐보다 낫다.
 * 2. 변수로 조립한 경로(`base + "/" + id`)는 잡지 못한다.
 *
 * 잘못 잡히면 EXPECTED에 이유와 함께 적는다.
 *
 * 판정 예(합성 입력):
 *   `/projects/{p}/clips/{c}` × `` `/clips/${id}/rights/recover` ``      → false ✓ (오탐 방지)
 *   `/projects/{p}/clips/{c}` × `` `/projects/${p}/clips/${c}` ``        → true  ✓
 *   `/projects/{p}/maiu/jobs/{j}` × `` `/projects/${p}/maiu/jobs` ``     → false ✓ (목록만 부름)
 */
function paramEndRegex(path) {
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const segs = path.replace(/^\//, "").split("/");
  if (!segs[segs.length - 1].startsWith("{")) return null;
  let out = "";
  segs.forEach((seg, i) => {
    const last = i === segs.length - 1;
    if (seg.startsWith("{")) out += last ? "/\\$\\{" : "/\\$\\{[^}]*\\}";
    else out += "/" + esc(seg);
  });
  return new RegExp(out);
}

const spec = JSON.parse(readFileSync(SPEC, "utf8"));
const blob = walk(FE).map((f) => readFileSync(f, "utf8")).join("\n");

const orphans = [];
for (const [path, ops] of Object.entries(spec.paths)) {
  const methods = Object.keys(ops)
    .filter((m) => ["get", "post", "patch", "put", "delete"].includes(m))
    .map((m) => m.toUpperCase())
    .join(",");
  const t = tail(path);
  if (t === null) {
    // 파라미터로 끝나는 경로는 보간 패턴으로 잰다(위 `paramEndRegex` 참조).
    const re = paramEndRegex(path);
    if (re && re.test(blob)) continue;
    if (EXPECTED[path]) continue;
    orphans.push({ path, methods });
    continue;
  }
  if (blob.includes(t)) continue;
  if (EXPECTED[path]) continue;
  orphans.push({ path, methods });
}

const total = Object.keys(spec.paths).length;
if (orphans.length === 0) {
  console.log(`✅ 라우트 ${total}종 — 화면이 안 부르는 것 없음 (예외 ${Object.keys(EXPECTED).length}건 제외)`);
} else {
  console.log(`⚠️  라우트 ${total}종 중 **화면이 한 번도 안 부르는 것 ${orphans.length}종**:\n`);
  for (const o of orphans) console.log(`  ${o.methods.padEnd(12)} ${o.path}`);
  console.log(`\n붙일지 걷을지 정한다. 안 부르는 게 정상이면 이 파일의 EXPECTED에 **이유와 함께** 적는다.`);
}
if (process.argv.includes("--strict") && orphans.length) process.exit(1);
