/**
 * 감사 로그가 raw event_type을 노출하는지 센다. BE가 내는 값과 FE 사전을 모두 대조한다.
 *
 * 스크립트로 두는 이유: 감사 로그·대시보드 최근 활동 화면은 모두 `features/audit/lib/event_label`의 사전으로
 * 문구를 그린다. 사전에 없으면 알림 없이 raw 값으로 대체한다(안전한 기본값이다).
 * 그래서 새 이벤트를 추가해도 아무것도 깨지지 않는다. 대신 `프로젝트 updated` 같은 문자열이
 * 화면에 그대로 나간다. 눈으로 훑으면 드물게 나는 이벤트가 계속 빠진다.
 * `tsc`와 테스트는 이 문제를 잡지 않는다.
 *
 * 읽고 판단하는 스크립트다. 누락이 있으면
 * 종료 코드 1을 낸다. 새 이벤트를 냈으면 사전에 표시 문구를 정하고, 화면에 나가지 않는 내부
 * 이벤트라면 그 이유를 여기 `EXPECTED`에 적는다.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/** 화면에 나가지 않아도 되는 이벤트 목록. 이유와 함께 적고, 이유 없이 빼지 않는다. */
const EXPECTED = new Set([
  // 지금은 없다. 이벤트를 검사에서 빼려면 화면에 나가지 않는 이유를 여기 적는다.
]);

/** 디렉터리를 재귀로 돌며 `.py` 파일 경로를 모은다. */
const walk = (dir) => {
  const out = [];
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".py")) out.push(p);
  }
  return out;
};

/**
 * f-string으로 조립되는 event_type이 실제로 가질 수 있는 값. 서버의 Literal·상수에서
 * 옮겨 적는다. 키는 `머리{}꼬리` 형태이고, 스크립트가 값을 하나씩 넣어 전체 event_type을 만든다.
 */
const ENUMS = {
  // _STAGE_BY_CALLER (services/maiu/reconciler/errors.py)와 대체값 unknown
  "maiu.stage.{}_failed": ["register", "poll_registration", "analyze", "poll_analyze",
                           "summary", "poll_summary", "unknown"],
  // ReviewVerdict (models/label.py)
  "review.{}": ["approve", "reject", "modify_and_approve"],
  // ProbeStatus 중 이벤트를 내는 두 값 (services/media/probe.py의 if)
  "video.probe_{}": ["rejected", "error"],
  // Verdict (services/caption_qc/rules.py). 캡션 품질검수의 세 가지 판정이다.
  "caption_qc.{}": ["pass", "needs_review", "reject_suggested"],
};

/** `ENUMS`에 없는 조립 위치다. 세지 못했다는 사실을 그대로 보고한다(0이라고 하지 않는다). */
const unknownTemplates = new Set();

const types = new Set();
for (const dir of ["apps/api-gateway/api_gateway", "apps/worker-io"]) {
  let files = [];
  try { files = walk(dir); } catch { continue; }
  for (const f of files) {
    const s = readFileSync(f, "utf8");
    for (const m of s.matchAll(/event_type\s*=\s*["']([a-z_]+\.[a-z_.]+)["']/g)) types.add(m[1]);
    /*
      f-string으로 조립되는 event_type도 센다. 리터럴만 보면 `review.approve` 같은 조립 이벤트의
      누락을 놓치고 게이트가 누락을 0건으로 잘못 보고한다.
      조립 값은 코드가 아니라 계약의 Literal이 정하므로 `ENUMS`에 적어 두고 조합한다.
      새 조립 위치를 만들면 `ENUMS`에 값을 적는다. 적지 않으면 그 이벤트가 검사에서 빠진다.
    */
    for (const m of s.matchAll(/event_type\s*=\s*f["']([a-z_]+\.[a-z_.]*)\{[^}]+\}([a-z_]*)["']/g)) {
      const [, head, tail] = m;
      const values = ENUMS[head + "{}" + tail];
      if (!values) { unknownTemplates.add(`f"${head}{…}${tail}"  (${f})`); continue; }
      for (const v of values) types.add(head + v + tail);
    }
  }
}

const src = readFileSync("apps/web/src/features/audit/lib/event_label.ts", "utf8");
/**
 * `event_label.ts`에서 `export const NAME: … = {` 블록의 첫 단계 키를 모은다.
 * 이벤트 종류(`EVENTS`)가 이 꼴이다.
 */
const blockKeys = (name) => {
  const start = src.indexOf(`export const ${name}:`);
  if (start < 0) return new Set();
  const open = src.indexOf("{", src.indexOf("= {", start));
  let depth = 0, end = open;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) { end = i; break; }
  }
  const out = new Set();
  for (const m of src.slice(open + 1, end).matchAll(/^\s*"?([a-z_][a-z_0-9.]*)"?\s*:\s*\{/gm)) out.add(m[1]);
  return out;
};
const known = blockKeys("EVENTS");

const missing = [];
for (const t of [...types].sort()) {
  if (EXPECTED.has(t)) continue;
  if (!known.has(t)) missing.push(`${t} — EVENTS 표에 없음`);
}

for (const t of [...unknownTemplates].sort()) {
  missing.push(`${t} — 조립 자리의 값 목록이 이 스크립트에 없어 **세지 못했다**`);
}

if (missing.length) {
  console.log(`⚠ event_type ${types.size}종 중 ${missing.length}종이 화면에 raw로 나갑니다:\n`);
  for (const m of missing) console.log("  " + m);
  console.log("\n→ features/audit/lib/event_label.ts에 말을 정해 주거나, 화면에 안 나가는 이유를 이 스크립트의 EXPECTED에 적으세요.");
  process.exitCode = 1;
} else {
  console.log(`✅ event_type ${types.size}종 — 전부 한국어 라벨이 있다 (예외 ${EXPECTED.size}건 제외)`);
}
