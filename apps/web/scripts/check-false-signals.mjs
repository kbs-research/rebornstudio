/**
 * 코드가 화면에 잘못된 신호를 남기는 경우 두 가지를 센다.
 *
 * `tsc`와 `vite build`는 이 문제를 잡지 않는다. 둘 다 정상적으로 컴파일되고 화면도 멀쩡해 보인다.
 *
 * 첫째, 모든 행이 같은 값인 열(`cell: () =>`처럼 행 인자를 받지 않는 열).
 * 행을 보지 않고 그리는 열은 공간만 차지하고 정보가 없는데, 읽는 사람은 그 열에 뜻이 있다고 믿는다.
 * 위반 0건을 유지하려고 이 검사를 둔다.
 *
 * 둘째, 상속되는 줄바꿈 유틸을 다시 붙이는 경우.
 * `word-break: keep-all`과 `overflow-wrap: anywhere`는 이미 `body`에 있고 상속된다.
 * `break-keep`을 붙인 요소와 붙이지 않은 요소의 계산값은 같아서(`keep-all`, `anywhere`), 또 붙이면 중복이고 특별한 곳이라는 잘못된 신호가 남는다.
 * `break-all`·`break-words`는 ID·경로처럼 어절이 없는 문자열에서 상속값을 일부러 바꾸는 유틸이라 세지 않는다.
 *
 * 대상을 밝히지 않는 파괴적 확인 대화상자(비가역 액션의 판단 근거)는 뜻을 봐야 해서 사람이 리뷰로 확인한다.
 * 가장 낮은 기준인 "작업 대상의 이름이 나오는가"도 오탐과 누락이 함께 난다.
 * - 오탐: 대상을 `<ClipIdentity title={…} start={…} />`처럼 자식 컴포넌트로 밝히는 확인창은
 *   값이 자식 컴포넌트의 속성이라 "본문에 보간이 있는가"로는 보이지 않는다. 이름을 컴포넌트로 뺀 좋은 구현이 검사에 걸린다.
 * - 누락: 본문이 "이 메시지를 회수합니다"뿐인 확인창도 버튼의
 *   `{recall.isPending ? "회수 중…" : "회수"}`를 보간으로 세어 통과한다. 진행 중 문구가 대상 이름으로 잘못 인식된다.
 * 오탐과 누락이 함께 나는 게이트는 신뢰할 수 없어서 `scripts/README.md`의 대응표에서 이 항목을 "사람"으로 둔다.
 *
 * 사용법:  node apps/web/scripts/check-false-signals.mjs   (소스만 읽으므로 빌드가 필요 없다)
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src/", import.meta.url).pathname;

/** 소스 트리의 `.ts`/`.tsx` 파일을 모두 모은다. */
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return /\.tsx?$/.test(e.name) ? [p] : [];
  });
}

/** 상속되는 값을 되풀이하는 유틸 목록. 유틸 이름을 키로, 잘못인 이유를 값으로 적는다. */
const INHERITED = {
  "break-keep": "`body`가 이미 `word-break: keep-all`이다. 계산값이 같다",
};

const findings = [];
for (const file of walk(SRC)) {
  const rel = file.slice(file.indexOf("apps/web/"));
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (/\bcell:\s*\(\s*\)\s*=>/.test(line)) {
      findings.push([rel, i + 1, "행 인자를 안 받는 `cell` — 전 행이 같은 값이 됩니다"]);
    }
    for (const [util, why] of Object.entries(INHERITED)) {
      if (new RegExp(`[\\s"'\`]${util}[\\s"'\`]`).test(line)) {
        findings.push([rel, i + 1, `\`${util}\` 은 상속됩니다 — ${why}`]);
      }
    }
  });
}

if (findings.length) {
  console.log(`잘못된 신호 ${findings.length}건:`);
  for (const [f, l, why] of findings) console.log(`  ${f}:${l}  ${why}`);
  console.log("\n  → 행마다 다른 값을 그리거나 열을 걷고, 상속되는 유틸은 지웁니다.");
  process.exit(1);
}
console.log("✅ 행 인자 없는 칸 없음 · 상속되는 줄바꿈 유틸 재선언 없음");
