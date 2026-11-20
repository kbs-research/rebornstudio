/**
 * 화면 문구에 내부 용어가 들어갔는지 검사하는 게이트.
 *
 * 이 검사는 낱말 사전 하나만 쓴다. 맥락 판단(번역체·AI체)은 기계가 할 수 없지만 낱말은 셀 수 있고,
 * 낱말만 막아도 내부 용어가 다시 들어올 여지가 줄어든다.
 *
 * 문구는 주석을 먼저 지운 뒤(줄·블록·`{/* … *\/}` 모두) 세 가지 방법으로 검사한다.
 * 한 방법만 쓰면 빠지는 경우가 생긴다.
 * 1. 조각: 남은 줄을 `<>{}"'`와 백틱으로 잘라 한글이 든 조각을 본다. 따옴표 안과 속성값이
 *    여기 걸린다. 짧은 조각도 건너뛰지 않는다(`버킷`·`이슈`는 두 글자다).
 * 2. 이어 붙인 본문: 태그 `<…>`와 식 `{…}`를 지우고 남은 것을 한 줄로 본다.
 *    `Job {n}건`은 조각으로 자르면 `Job `(한글 없음)과 `건`으로 흩어져 어느 방법에도 걸리지 않는다.
 * 3. 낱말 자체: 조각 전체를 사전 낱말과 비교한다. 표 머리의 `"Job"`은 한글이 없어 1·2에
 *    걸리지 않지만 화면에는 그대로 뜬다. 사전의 영문 낱말은 한글 조건만으로는 잡을 수 없다.
 *
 * 예외: 클라우드 콘솔의 실제 항목 이름은 그대로 쓴다. 운영자가 그 콘솔에서 같은 이름을 보고
 * 값을 옮겨 적기 때문이다(`자격증명 연결` 화면의 `엔드포인트`). 예외는 낱말 단위로 적고
 * 이유를 남긴다. 파일을 통째로 빼면 그 파일의 다른 용어까지 함께 가려진다.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../", import.meta.url).pathname;
const SRC = join(ROOT, "src");

/** 내부 용어를 키로, 화면에서 대신 쓸 말을 값으로 둔 사전. 값은 검사 결과 안내에만 쓴다. */
const JARGON = {
  "큐": "대기열",
  "버킷": "저장소",
  "Job": "등록 묶음",
  "이슈": "문제",
  "파이프라인": "처리 과정",
  "스냅숏": "그 시점 기록",
  "폴백": "대체 동작",
  "프로비저닝": "만들기",
  "페이로드": "보내는 값",
  "엔드포인트": "주소",
  "스택": "방식",
  "스키마": "규격 정의",
};

/** 파일별 낱말 단위 예외 목록. 이유를 함께 적고, 이유 없이 목록에서 빼지 않는다. */
const EXEMPT = {
  "src/features/platform/pages/connections.tsx": {
    words: ["엔드포인트"],
    why: "클라우드 콘솔이 그 이름으로 부르는 입력 칸 — 옮겨 쓰면 콘솔에서 같은 칸을 못 찾는다",
  },
};

const files = [];
/** `src` 아래의 `.ts`/`.tsx` 파일(테스트 제외)을 재귀로 돌며 `files`에 모은다. */
(function walk(dir) {
  for (const e of readdirSync(dir)) {
    const f = join(dir, e);
    if (statSync(f).isDirectory()) walk(f);
    else if (/\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f)) files.push(f);
  }
})(SRC);

/**
 * 한 줄에서 주석을 지운 텍스트와 블록 주석 상태를 돌려준다.
 * `inBlock`은 여러 줄에 걸친 블록 주석 안에 있으면 true다.
 *
 * 이 저장소의 docstring은 한국어라서 지우지 않으면 모두 오탐이 된다. JSX 주석
 * `{/* … *\/}`도 같은 블록 문법이라 함께 지운다.
 */
function stripComments(line, inBlock) {
  let out = "";
  let i = 0;
  while (i < line.length) {
    if (inBlock) {
      const end = line.indexOf("*/", i);
      if (end === -1) return { text: out, inBlock: true };
      i = end + 2;
      inBlock = false;
    } else {
      const bl = line.indexOf("/*", i);
      const ln = line.indexOf("//", i);
      if (bl !== -1 && (ln === -1 || bl < ln)) {
        out += line.slice(i, bl);
        i = bl + 2;
        inBlock = true;
      } else if (ln !== -1) {
        out += line.slice(i, ln);
        return { text: out, inBlock: false };
      } else {
        out += line.slice(i);
        return { text: out, inBlock: false };
      }
    }
  }
  return { text: out, inBlock };
}

const hits = [];
for (const f of files) {
  const rel = f.slice(ROOT.length);
  const exempt = EXEMPT[rel]?.words ?? [];
  let inBlock = false;
  readFileSync(f, "utf8")
    .split("\n")
    .forEach((line, i) => {
      const stripped = stripComments(line, inBlock);
      inBlock = stripped.inBlock;

      const pieces = stripped.text.split(/[<>{}"'`]/).map((t) => t.trim());
      // 태그와 식을 지우고 이어 붙인다. `Job {n}건`처럼 식이 낱말과 수를 갈라놓은 경우를 잡는다.
      const joined = stripped.text.replace(/<[^>]*>/g, "").replace(/\{[^}]*\}/g, "").trim();

      const seen = new Set();
      const flag = (bad, good, text) => {
        if (seen.has(bad)) return;
        seen.add(bad);
        hits.push(`${rel}:${i + 1}  «${bad}» → «${good}»   ${text.trim().slice(0, 60)}`);
      };
      for (const [bad, good] of Object.entries(JARGON)) {
        if (exempt.includes(bad)) continue;
        const piece = pieces.find((t) => /[가-힣]/.test(t) && t.includes(bad));
        if (piece) flag(bad, good, piece);
        else if (/[가-힣]/.test(joined) && joined.includes(bad)) flag(bad, good, joined);
        else if (pieces.some((t) => t === bad)) flag(bad, good, bad);
      }
    });
}

if (hits.length === 0) {
  console.log(`✅ 화면 문구에 내부 용어 없음 (사전 ${Object.keys(JARGON).length}종 · 예외 ${Object.keys(EXEMPT).length}파일)`);
  process.exit(0);
}
console.error(`❌ 내부 용어가 화면 문구에 ${hits.length}건`);
for (const h of hits) console.error(`   ${h}`);
console.error("\n   화면은 작업자가 읽습니다. 콘솔에서 같은 이름을 봐야 하는 화면이면 이 파일 맨 위 EXEMPT 에 이유와 함께 적습니다.");
process.exit(1);
