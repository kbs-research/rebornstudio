/**
 * 화면 문구가 규칙을 지키는지 검사한다.
 *
 * 말투 전반은 기계로 셀 수 없지만, 규칙이 명확한 일부는 셀 수 있다.
 *
 * 아래 규칙 중 하나라도 걸리면 exit 1이다.
 *  1. 서버 문자열의 마크다운. 화면은 문자열을 평문으로 그려서 별표가 그대로 찍힌다.
 *  2. 화면 문구의 1인칭(`우리`·`저희`)과 `벤더`. 읽는 사람은 누가 누구인지 알 수 없다.
 *     화면 문구의 상당수는 따옴표 없는 JSX 텍스트라서, 주석을 뺀 나머지 전부를 따옴표 밖 JSX 텍스트까지 포함해 검사한다.
 *  3. 표기 불일치. `해주세요`와 `잠시 후`로 통일하고 `해 주세요`·`잠시 뒤`를 막는다.
 *  4. 말줄임 표기 불일치. 타이포그래피상 맞는 `…`로 통일하고 `...`를 막는다.
 *  5. 반말·구어체 종결. 앱은 `~합니다`체다. 코드 옆 문자열은 주석처럼 쓰기 쉬워서
 *     `…경우가 많다`·`…있어요`가 섞여 들어간다.
 *  6. 내부 값 노출. 역할 키(`super_admin`)·DB 컬럼·함수명(`check_row`)은 화면에서 쓰는 이름이 따로 있다.
 *  7. `내용 — 내용` 이어 붙이기. 결과와 부연을 대시 하나로 붙이면 한 문장인지 두 문장인지 모를 덩어리가 되고,
 *     좁은 폭에서는 대시만 줄 끝에 남는다. 그래서 문장을 끊게 하고, 하이픈·엔대시로 이은 것도 함께 막는다.
 *  8. 구어체 낱말. 금지한 낱말을 낱말 사전으로 검사한다.
 *  9. 제목은 명사구로 쓴다. `<h1~h3>`이 `…것`·`…나`로 끝나면 문장이다.
 *     제목만 검사하고, 빈 상태·본문은 `~합니다`가 맞다.
 *     빈 상태 머리글과 제목 옆 괄호 부제도 같은 규칙으로 검사한다.
 * 10. 용어 불일치. 한 기능을 두 이름으로 부르지 않는다. 낱말 사전으로 본다.
 * 11. 중의적 낱말 `돕니다`. `돌다`와 `돕다`의 활용형이 같다.
 *
 * 5와 7은 따옴표 안만 보면 JSX 본문의 위반을 놓치므로(2와 같은 문제) 따옴표 안과 JSX 본문을 모두 검사한다.
 * 5의 JSX 쪽은 어미를 나열하는 대신 `~다`가 절 끝에 오는지 확인하고, `보다`·`마다`는 뺀다.
 *
 * 3·4의 검사 범위는 한글이 든 문자열 리터럴(큰따옴표, 백틱 템플릿)이다. 범위를 좁혀야
 * spread 연산자 `...`를 건드리지 않는다.
 *
 * 사용법:  node scripts/check-copy.mjs
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const WEB = new URL("../src", import.meta.url).pathname;
const API = new URL("../../api-gateway/api_gateway", import.meta.url).pathname;

/**
 * 단위 테스트(`*.test.ts`)는 화면에 나가지 않으므로 검사에서 뺀다.
 * 테스트 이름은 개발자가 읽는 설명이라, 포함하면 `"지난 시각은 만료로 적는다"` 같은 이름이 반말 종결로 걸린다.
 */
const isTest = (p) => /\.test\.tsx?$/.test(p);

/** 디렉터리를 재귀로 돌며 `re`에 맞는 파일을 모은다. 테스트 파일은 뺀다. */
function walk(dir, re) {
  let out = [];
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) out = out.concat(walk(p, re));
    else if (re.test(p) && !isTest(p)) out.push(p);
  }
  return out;
}

/** 주석 줄이면 true. 주석은 근거를 적는 곳이라 마크다운과 1인칭이 있어도 된다. */
const isComment = (l) => /^\s*(\/\/|\/?\*|#)/.test(l);

/**
 * 파일을 줄 단위로 읽되 블록 주석 안은 건너뛴다. `[줄, 번호]` 목록을 돌려준다.
 *
 * `isComment`는 한 줄 주석만 확인한다. 블록 주석은 이유를 적으면서 이전 문구를 인용하는 일이 잦고
 * (`"토큰이 죽었다"` 같은), 그 인용이 문자열 검사에 그대로 걸린다. 오탐이 잦은 게이트는 곧 무시된다.
 */
function codeLines(src) {
  const out = [];
  let blk = false;
  src.split("\n").forEach((line, i) => {
    const t = line.trim();
    if (blk) {
      if (t.includes("*/")) blk = false;
      return;
    }
    // JSX 주석은 `{/*`로 시작한다. `/*`만 확인하면 JSX 주석 안의 인용구가 모두 오탐이 된다.
    if (t.startsWith("/*") || t.startsWith("{/*")) {
      if (!t.includes("*/")) blk = true;
      return;
    }
    if (isComment(line)) return;
    out.push([line, i]);
  });
  return out;
}

/**
 * 주석을 지운 사본을 줄 배열로 돌려준다. 코드와 문구만 남긴다.
 *
 * 여러 줄 블록 주석의 가운데 줄은 `*`로 시작하지 않을 수 있고 JSX 주석은 `{/*`로 시작해서,
 * 줄 시작만 보는 `isComment`는 두 주석을 놓친다. 두 곳 모두 1인칭을 적는 곳이라 블록 상태를 기억하며 지운다.
 * 출력한 위치의 줄 번호가 맞도록 지운 곳은 빈 줄로 남긴다.
 */
function stripComments(src) {
  const out = [];
  let inBlock = false;
  for (const line of src.split("\n")) {
    let kept = "";
    for (let i = 0; i < line.length; i++) {
      if (inBlock) {
        if (line[i] === "*" && line[i + 1] === "/") { inBlock = false; i++; }
        continue;
      }
      if (line[i] === "/" && line[i + 1] === "*") { inBlock = true; i++; continue; }
      if (line[i] === "/" && line[i + 1] === "/") break;
      kept += line[i];
    }
    out.push(kept);
  }
  return out;
}

/*
  주석을 거르는 함수는 쓰는 곳이 달라 두 개다. 돌려주는 형태가 다르고, 합치면 한쪽 규칙의 줄 번호가 어긋난다.
  `codeLines`는 주석 줄을 건너뛰고 `[줄, 번호]`를 준다. 문자열 리터럴(`quoted`)만 보는 규칙(3·4, 따옴표 쪽 5·6·7, 11)이 쓴다.
  `stripComments`는 주석만 지우고 줄 번호를 보존한 사본을 준다. 따옴표 밖 JSX 텍스트까지 보는 규칙(2, JSX 쪽 5·6·7, 8·9·10, 빈 상태 머리글)이 쓴다.
*/

const fails = [];

/*
  1. 서버 문자열. `description=`·`hint=`·`title=`과 dict 키(`"what": "…"`)처럼 화면에 그대로 나가는 필드만 검사한다.
  docstring과 주석은 개발자가 읽는 곳이라 마크다운과 대시가 정상이고, 대시로 이은 서버 한글 문자열은 대부분 여기에 있어 키로 좁힌다.
  `"what":`은 이름 뒤에 닫는 따옴표가 와서 `\s*[=:]`만으로는 맞지 않는다. 게이트를 고치면 일부러 넣은 위반이 잡히는지 확인한다.
  알림 제목(`캡션이 반려됐습니다 — 재작업 필요`)은 서버가 만들어 화면 규칙에 걸리지 않으므로 대시·반말도 여기서 검사한다.
*/
const SERVER_KEYS = /(description|hint|unavailable_reason|label|what)"?\s*[=:]/;
/*
  대시·반말은 오탐을 줄이려고 화면에만 나가는 더 좁은 키로 검사한다.
  `description=`은 프리셋 설명뿐 아니라 OpenAPI 파라미터 문서(`Query(description=…)`)와 목 벤더 응답의 장면 요약에도 쓰이고,
  `detail`·`reason`·`message`는 로그와 페이로드에도 흔하다. 그래서 `message`·`detail`의 위반은 이 검사가 잡아내지 못한다.
  범위를 넓히려면 오탐 없는 판별 방법을 먼저 찾는다.
*/
const SCREEN_KEYS = /(title|body|hint|unavailable_reason|note|error)"?\s*[=:]/;
for (const f of walk(API, /\.py$/)) {
  readFileSync(f, "utf8").split("\n").forEach((line, i) => {
    if (isComment(line)) return;
    if (!SERVER_KEYS.test(line) && !SCREEN_KEYS.test(line)) return;
    const at = { file: f.replace(API, "api_gateway"), line: i + 1, text: line.trim() };
    // 마크다운은 `**굵게**` 형태일 때만 잡는다. 별 뒤에 공백·`(`·`{`가 오는 경우는 뺀다.
    // `/\*\*/`로 잡으면 파이썬 딕셔너리 언패킹(`{**meta, …}`)이 오탐으로 걸린다.
    if (/\*\*[^*\s({][^*]*\*\*/.test(line)) fails.push({ ...at, why: "서버 문구에 마크다운" });
    if (!/[가-힣]/.test(line) || !SCREEN_KEYS.test(line)) return;
    if (/\S\s—\s\S/.test(line))
      fails.push({ ...at, why: "서버 문구를 `내용 — 내용`으로 이어 붙임 — 문장을 끊는다" });
    /* 줄은 `)`·`,`로 끝나는 경우가 많아 줄 끝에 앵커를 걸 수 없으므로 반말은 따옴표 안쪽만 검사한다.
       끝이 `{name}`이면 종결 어미가 가려지므로 `{…}` 자리표시자는 지우고 센다. */
    for (const m of line.matchAll(/"([^"\n]{6,200})"/g)) {
      const q = m[1].replace(/\{[^}]*\}/g, "").trim();
      if ((q.match(/[가-힣]/g) ?? []).length < 6) continue;
      if (/(?<![보마])다[.!…]?$/.test(q) && !/니다[.!…?]?$/.test(q))
        fails.push({ ...at, why: "서버 문구가 반말 종결 — ~합니다체로", text: q });
    }
  });
}

// 화면에 내보내지 않는 내부 값(역할 키·DB 컬럼·함수명).
// JSX 본문 규칙과 따옴표 규칙이 함께 쓴다.
const KEYS = /\b(super_admin|project_admin|legal_reviewer?|global_role|rights_status|approval_status|prep_stage|check_row|work_item|last admin)\b/;

// ── 2. 화면 문구의 1인칭 ────────────────────────────────────────────────────
/**
 * 한 줄에서 화면 문구가 될 수 있는 리터럴(큰따옴표, 백틱 템플릿)을 모은다.
 *
 * 큰따옴표만 보면 `실행 ${n}회 중 성공 ${ok}`처럼 백틱으로 쓴 문구가
 * 종결·내부 값·줄표·마크다운 규칙 어디에도 걸리지 않는다.
 *
 * `${…}`는 값이라 지운다. 지우지 않으면 `${n - 1}` 같은 뺄셈이 대시 규칙에 걸린다.
 */
function quoted(line) {
  const out = [];
  for (const m of line.match(/"[^"\n]*"/g) ?? []) out.push(m);
  for (const m of line.match(/`[^`\n]*`/g) ?? []) {
    const v = m.replace(/\$\{[^}]*\}/g, " ").replace(/^`|`$/g, "");
    if (/[가-힣]/.test(v)) out.push(`"${v}"`);   // 규칙들이 큰따옴표로 감싼 형태를 기대한다.
  }
  return out;
}

for (const f of walk(WEB, /\.tsx?$/)) {
  stripComments(readFileSync(f, "utf8")).forEach((line, i, all) => {
    // `우리`로 시작하는 다른 낱말이 없어서 어절 경계는 따로 확인하지 않는다.
    // 오탐이 나오면 그때 좁힌다.
    if (/우리|저희/.test(line))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "화면 문구에 1인칭", text: line.trim() });
    /*
      `벤더`도 같은 부류다.
      화면에서는 그 폼의 라벨인 `프로바이더`를 쓴다.
    */
    if (/벤더/.test(line))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "화면 문구에 `벤더` — 화면이 부르는 말로(예: 프로바이더)", text: line.trim() });
    /*
      구어체 낱말: 화면에 쓰지 않는 낱말이다(`안 줄고 있음`은 정체, `아무도 안 가져간`은 미착수로 쓴다).
      규칙만 있고 게이트가 없으면 새 위반이 계속 들어오므로 낱말 사전으로 센다. 말투 전반은 여전히 사람이 확인한다.
    */
    const COLLOQUIAL = [
      [/묵은\s/, "묵은 → 장기 대기"],
      [/손이 필요/, "손이 필요한 → 조치 필요"],
      [/안 줄고 있/, "안 줄고 있음 → 정체"],
      [/아무도 안 가져/, "아무도 안 가져감 → 미착수"],
      [/남은 것 없/, "남은 것 없음 → 잔여 없음"],
      [/아직 모름/, "아직 모름 → 측정 전"],
      // 값 표시에 쓰인 메모 말투.
      [/안 냄/, "아직 안 냄 → 집계 전"],
      [/안 셌/, "아직 안 셌음 → 집계 전"],
      [/안 쟀/, "안 쟀음 → 측정 전"],
    ];
    for (const [re, hint] of COLLOQUIAL)
      if (re.test(line) && /[가-힣]/.test(line))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: `구어체 — ${hint}`, text: line.trim() });

    /*
      한 기능은 한 이름으로 부른다. 이름이 하나 어긋나면 그 이름을 가리키는 문장도 따라 늘어난다.
      비식별화 스위치의 이름이 절 제목·단계·통계와 다르면 스위치를 켤 때 무엇이 켜지는지 알 수 없다.
      동사(`얼굴을 가린`·`지금 가리기`)는 설명이라 잡지 않는다.
    */
    const TERMS = [
      [/얼굴 가리기/, "얼굴 가리기 → 비식별화 (기능 이름은 하나)"],
    ];
    for (const [re, hint] of TERMS)
      if (re.test(line))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: `용어 갈림 — ${hint}`, text: line.trim() });

    /*
      카드·섹션 제목은 명사구로 쓴다. `<h1~h3>`이 `…것`·`…나`·`…가`·`…까`로 끝나면 문장이다(예: `설정할 수 있는 것`, `큐가 언제 비나`).
      빈 상태·안내 본문은 `~합니다`가 맞으므로 제목만 검사한다. 같은 기준으로 재면 본문을 명사구로 만들게 되어 규칙끼리 충돌한다.
    */
    const heading = line.match(/<h[1-3][^>]*>([^<>{}]{2,30})<\/h[1-3]>/);
    if (heading) {
      const t = heading[1].trim();
      if (/[가-힣]/.test(t) && /(것|나|가|까)$/.test(t))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "제목이 서술체 — 명사구로", text: t });
    }

    /*
      제목 옆 괄호에 설명을 넣지 않는다. `font-normal` 부제 span 전체가 괄호이면 제목만으로 뜻이 전해지지 않는다는 신호다.
      분모는 남기고 명사구 안으로 합친다. 예를 들어 `처리 추이 (승인 분량)`은 `승인 분량 추이`로 쓰고, 합칠 것이 없으면 뺀다.
      값을 담는 괄호(`클립 (12)`·`분석 비용 (${stack.label})`)는 그대로 두므로 한글 리터럴이 든 괄호만 검사한다.
      부제가 의문형(`…하나`)인 것도 함께 잡는다.
    */
    const sub = line.match(/font-normal[^>]*>\s*(\(?)([^<>{}]{2,60}?)\)?\s*<\/span>/);
    if (sub && /[가-힣]/.test(sub[2])) {
      const t = sub[2].trim();
      if (sub[1] === "(")
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "괄호 부제 — 제목에 접거나 뺀다", text: t });
      else if (/(나|까)$/.test(t))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "부제가 의문형 — 명사구로", text: t });
    }

    /*
      따옴표 밖 JSX 텍스트의 대시도 검사한다. 아래 큰따옴표 규칙만으로는 `<p>…따라갑니다 — 그 뒤 …</p>` 형태를 잡지 못한다.
      한글이 있는 줄에서 양쪽에 공백이 있는 대시만 보므로 값 없음 표시(`—`)와 범위(`0:12–1:25`)는 걸리지 않는다.
      따옴표 안은 아래 규칙이 따로 검사하므로 지우고 검사한다.
    */
    const jsxText = line.replace(/"[^"]*"/g, "").replace(/'[^']*'/g, "").replace(/`[^`]*`/g, "");
    if (/[가-힣]/.test(jsxText) && /\S\s—\s\S/.test(jsxText))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "`내용 — 내용` 이어 붙이기 — 문장을 끊거나 괄호로", text: line.trim() });
    // 하이픈·엔대시도 함께 본다(아래 따옴표 규칙과 같은 이유). `{…}` 표현식은 지운 뒤 검사한다.
    if (/[가-힣]\s[-–]\s\S/.test(jsxText.replace(/\{[^{}]*\}/g, "")))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "`내용 - 내용` 이어 붙이기 — 문장을 끊거나 괄호로", text: line.trim() });
    /*
      위 두 규칙은 한글 옆의 대시를 검사하므로, 화면에서는 `A — B`가 되지만 문자열 안에 한글이 없는 두 형태를 따로 검사한다.
      `[badge, title].join(" — ")`는 이어 붙이는 대시가 문자열과 따로 있어서, sr-only 제목도 `완료 — 92건`으로 읽힌다.
      `` `— ${n}번째 문장` ``은 대시로 시작해 앞 텍스트에 이어 붙어 `지적 내용 — 3번째 문장`이 된다.
    */
    if (/\.join\(\s*["'`] — ["'`]\s*\)/.test(line))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "대시로 잇는 join — 쉼표나 공백으로", text: line.trim() });
    if (/["'`]— \S/.test(line))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "대시로 시작하는 문구 — 앞 텍스트에 이어 붙는다, 괄호로", text: line.trim() });
    /*
      줄 끝에 걸린 대시도 검사한다. `…입니다 —`로 줄을 끊고 다음 줄에서 문장을 이으면 화면에서는 `A — B`가 되는데,
      한 줄만 보는 규칙에는 대시 오른쪽이 비어 있어 걸리지 않는다.
      JSX 속성이 줄바꿈된 경우를 거르려고 다음 줄에도 한글이 있을 때만 검사한다.
    */
    if (/[가-힣]/.test(jsxText) && /\s—\s*$/.test(jsxText) && /[가-힣]/.test(all[i + 1] ?? ""))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "줄 끝 대시 — 다음 줄과 이어져 `내용 — 내용`이 된다", text: line.trim() });
    /*
      내부 값도 JSX 본문에서 검사하고, 아래 따옴표 규칙과 같은 목록을 쓴다.
      역할 화면의 설명글처럼 태그로 감싼 문장(`관리자(project_admin)는 …`, `<h3>전역 역할 (global_role)</h3>`)은 따옴표 밖에 있다.
    */
    if (/[가-힣]/.test(jsxText) && KEYS.test(jsxText))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "내부 값 노출 — 화면이 부르는 말로", text: line.trim() });
    /*
      JSX 텍스트의 반말 종결도 검사한다. 문장을 태그로 감싸 쓰면 따옴표 규칙이 볼 수 없고, 코드 옆이라 주석처럼 쓴 문장이 그대로 화면에 나간다.
      나열한 어미 목록은 언제나 모자라므로, `~다`가 절 끝(줄 끝, 또는 태그·표현식 앞)에 오는지 확인한다. `~합니다`는 통과한다.
      문장 중간의 `평균보다 높습니다`는 뒤에 글자가 이어져 걸리지 않고, 줄바꿈 위치에 걸린 `기준값보다`·`15분마다`만 남으므로 `보다`·`마다`를 뺀다.
    */
    const BANMAL = /[가-힣](?<![보마])다[.!]?(?=\s*(?:<|\{|$))/;
    const koLine = (jsxText.match(/[가-힣]/g) ?? []).length;
    if (koLine >= 6 && !/니다[.…!?]?\s*(?:<|\{|$)/.test(jsxText) && BANMAL.test(jsxText))
      fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "반말 종결 — ~합니다체로", text: line.trim() });
  });
}

/*
  9. 빈 상태 머리글. `EmptyState`의 `message`는 화면 가운데 굵게 뜨는 상태의 이름이라 `이벤트 없음`처럼 명사구로 쓴다.
  같은 줄이나 바로 위 줄의 `message="…"`만 보면 삼항·백틱·여러 줄(`q ? "검색 결과가 없습니다" : …`)을 놓치므로,
  `<EmptyState … />` 태그 단위로 잘라 머리글 속성(`message`, 래퍼가 넘기는 `text`) 안의 모든 문자열 리터럴을 검사한다.
  `description`·`note`는 할 일을 안내하는 문장이라 뺀다. `AccessDenied`처럼 `message`를 `description`으로 넘기는 컴포넌트가 있어 검사할 태그 이름을 허용 목록으로 둔다.
*/
/*
  태그의 끝(`/>`)은 찾지 않는다. `icon={<MessageSquare size={20} />}`처럼 속성 안에 자기 닫는 태그가 있으면
  그 `/>`가 먼저 걸려 `message=`에 닿기 전에 매치가 끊긴다.
  대신 태그 뒤 700자를 잘라 그 안에서 머리글 속성을 찾고, 다음 빈 상태 태그를 넘어가면 버린다.
*/
const HEADLINE_TAGS = /<(EmptyState|ErrorState|EmptyNote)\b/g;
const NEXT_PROP = /\s(?:description|note|onRetry|icon|action|inline|className)=/;
for (const f of walk(WEB, /\.tsx?$/)) {
  const src = stripComments(readFileSync(f, "utf8")).join("\n");
  for (const m of src.matchAll(HEADLINE_TAGS)) {
    // 검사 범위는 정규식 매치에 넣지 않고 직접 자른다. 범위까지 매치에 넣으면 `lastIndex`가
    // 그만큼 밀려 바로 뒤 태그를 건너뛴다.
    const win = src.slice(m.index, m.index + 700);
    const at = win.search(/\s(?:message|text)=/);
    if (at < 0) continue;
    // 다음 빈 상태 태그를 지나쳐 잡은 속성이면 버린다. 그 태그는 자기 차례에 검사된다.
    // 자기 태그 이름은 빼고 찾는다. 빼지 않으면 언제나 자기 자신이 걸려 모두 건너뛴다.
    const before = win.slice(m[0].length, at);
    if (/<(EmptyState|ErrorState|EmptyNote)\b/.test(before)) continue;
    const head = win.slice(at).match(/\s(?:message|text)=([\s\S]*)/);
    if (!head) continue;
    const cut = head[1].search(NEXT_PROP);
    const region = cut > 0 ? head[1].slice(0, cut) : head[1];
    const line = src.slice(0, m.index).split("\n").length;
    for (const lit of region.match(/["`][^"`\n]*["`]/g) ?? []) {
      const t = lit.slice(1, -1).replace(/\$\{[^}]*\}/g, "").trim();
      if (/[가-힣]/.test(t) && /(니다|하세요|해요|세요)[.!]?$/.test(t))
        fails.push({ file: f.replace(WEB, "src"), line, why: "빈 상태 머리글이 문장 — 명사구로", text: t });
    }
  }
}

// ── 11. 중의적인 낱말 `돕니다` ─────────────────────────────────────────────
//
// `돕니다`는 `돌다`(실행된다)와 `돕다`(도움이 된다)의 활용형이 같다. 문장 안에서는 문맥으로
// 구분되지만, 칩·배지처럼 홀로 쓰이면 사용자는 "도움이 됩니다"로 읽는다.
//
// 대신 쓸 말은 무엇이 실행되는지에 따라 다르다. 검사면 `검사합니다`, 단계·잡이면 `실행됩니다`.
for (const f of walk(WEB, /\.tsx?$/)) {
  codeLines(readFileSync(f, "utf8")).forEach(([line, i]) => {
    for (const q of quoted(line) ?? []) {
      if (!q.includes("돕니다")) continue;
      fails.push({
        file: f.replace(WEB, "src"), line: i + 1,
        why: "중의적 — `돕니다`는 돌다/돕다가 같은 꼴 (검사합니다·실행됩니다)", text: q,
      });
    }
  });
}

// ── 3·4. 표기 불일치: 소수 쪽 표기를 막는다 ────────────────────────
// spread `...`를 건드리지 않도록 한글이 든 문자열 리터럴(큰따옴표, 백틱 템플릿)만 검사한다.
// `해 주세요`(`해주세요`로 쓴다)·`...`(`…`로 쓴다)가 있으면 실패다.
// 같은 반복문에서 5(반말 종결)·6(내부 값)·7(대시)의 따옴표 쪽 검사도 한다.
for (const f of walk(WEB, /\.tsx?$/)) {
  codeLines(readFileSync(f, "utf8")).forEach(([line, i]) => {
    for (const q of quoted(line) ?? []) {
      if (!/[가-힣]/.test(q)) continue;
      if (q.includes("해 주세요"))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "존대 갈림 — 해주세요로", text: q });
      /*
        시점 표기 불일치: 에러 문구 사전(`shared/api/error_text.ts`)과 전역 토스트가 `잠시 후`를 쓰므로 `잠시 뒤` 대신 `잠시 후`를 기준으로 한다.
        토스트와 본문 실패 상태가 한 화면에 함께 뜨므로, 같은 실패를 두 표현으로 쓰면 사용자는 다른 일이 난 줄 안다.
      */
      if (q.includes("잠시 뒤"))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "시점 갈림 — 잠시 후로", text: q });
      if (q.includes("..."))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "말줄임 — …로 (spread 아님, 문자열 안)", text: q });
      /*
        반말·구어체 종결을 막는다. 앱은 `~합니다`체다.
        주의: `q`는 따옴표를 포함하므로(`"…"`) 따옴표 안쪽만 떼어 검사한다. `$`로 끝을 앵커하면 닫는 따옴표에 걸려 아무것도 잡히지 않는다.
        게이트를 고치면 일부러 넣은 위반이 실제로 잡히는지 확인한다.
      */
      const body = q.slice(1, -1).trim();
      // 한글은 전체 글자 수로 센다. `{6,}`는 연속된 글자를 요구해서 띄어쓰기가 있으면 맞지 않는다
      // (`재시도로 풀리는 경우가 많다`가 검사를 통과한다).
      const koCount = (body.match(/[가-힣]/g) ?? []).length;
      if (/(?:다|있다|없다|한다|된다|많다|아니다)[.…]?$/.test(body) && koCount >= 6 && !/니다[.…!?]?$/.test(body))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "반말 종결 — ~합니다체로", text: q });
      if (/(있어요|없어요|해요|예요|네요|거예요)[.…!?]?$/.test(body))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "구어체 종결 — ~합니다체로", text: q });
      /*
        `내용 — 내용`으로 두 부분을 잇지 않는다. 결과와 부연을 대시 하나로 붙이면 한 문장인지 두 문장인지 모를 덩어리가 되고, 좁은 폭에서는 대시 뒤가 따로 줄바꿈된다.
        문장을 끊거나(`…입니다. …`), 결과와 부연을 따로 받는 인자가 있으면 그 인자를 쓴다(`toast(종류, 결과, 부연)`). 제목 옆 서술 괄호는 금지라 괄호로 내리지 않는다.
        양쪽에 공백이 있는 대시만 검사하므로 값 없음 홑대시(`—`)와 범위(`0:12–1:25`)는 걸리지 않는다.
      */
      if (/[가-힣]/.test(q) && /\S\s—\s\S/.test(q))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "`내용 — 내용` 이어 붙이기 — 문장을 끊는다", text: q });
      /*
        위 규칙은 대시 양쪽에 글자를 요구하므로 뒷부분이 다음 표현식이면 잡지 못한다. `` `…반려됐습니다 — ` ``는 줄표 뒤가 문자열의 끝이고 이어지는 말은 옆 `{…}`에 있다.
        화면 문구가 줄표로 끝나는 경우는 이어 붙이기뿐이므로 줄표로 끝나거나 시작하는 문구를 따로 검사한다.
        값 없음 표시(`—` 한 글자)는 한글이 없어 걸리지 않는다.
      */
      if (/[가-힣]/.test(q) && /(\s—\s*["\'`]?$|^["\'`]?\s*—\s)/.test(q))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "줄표로 끝나는 문구 — 뒤와 이어 붙이는 자리다", text: q });
      /*
        규칙이 대시만 막으면 같은 문장을 하이픈으로 쓰게 되므로(`내용 - 내용`) 하이픈·엔대시로 이은 것도 막는다.
        `${n - 1}번` 같은 뺄셈이 걸리지 않도록 왼쪽에 한글을 요구하고 `${…}`를 먼저 지운다.
        시각 범위(`0:12–1:25`)는 공백이 없어 걸리지 않는다.
      */
      const noExpr = q.replace(/\$\{[^}]*\}/g, "");
      if (/[가-힣]\s[-–]\s\S/.test(noExpr))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "`내용 - 내용` 이어 붙이기 — 문장을 끊거나 괄호로", text: q });
      // 내부 값이 문장에 섞였는지 검사한다. 역할 키·DB 컬럼·함수명 대신 화면에서 쓰는 이름을 써야 한다.
      if (/[가-힣]/.test(q) && KEYS.test(q))
        fails.push({ file: f.replace(WEB, "src"), line: i + 1, why: "내부 값 노출 — 화면이 부르는 말로", text: q });
    }
  });
}

if (fails.length) {
  console.log(`❌ 관례를 어긴 문구 ${fails.length}건:\n`);
  for (const x of fails) console.log(`  ${x.file}:${x.line}  ${x.why}\n      ${x.text.slice(0, 100)}`);
} else {
  console.log("✅ 서버 문구 마크다운 없음 · 화면 1인칭·벤더 없음 · 존대/말줄임/시점 통일(해주세요·…·잠시 후) · 종결 ~합니다(따옴표·JSX 본문) · 내부 값 없음 · 대시로 두 토막 잇기 없음 · 용어 갈림 없음");
}

process.exit(fails.length ? 1 : 0);
