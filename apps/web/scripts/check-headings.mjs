/**
 * 절 제목이 heading 요소로 출력되는지 검사한다.
 *
 * 구조가 없으면 스크린리더와 브라우저 개요가 페이지를 한 덩어리로 읽고, 코드에서도 절과 라벨을 구분할 수 없다.
 * 태그가 span이어도 정상적으로 컴파일되므로 `tsc`와 `vite build`는 이 문제를 잡아내지 못한다.
 * 절 제목을 `<span class="text-callout font-semibold text-label-2">`로 쓰면 보기에만 제목이고
 * 페이지 전체에 `h1` 하나만 남는다.
 *
 * 절 제목은 모양으로 찾는다. `font-semibold`, 타입 크기(`text-callout` 등), 글자색(`text-label*`)이
 * 함께 붙은 요소는 이 저장소에서 사실상 제목이다. 굵기만 보도록 범위를 넓히면 값·배지·강조가 모두 걸린다.
 *
 * 같은 모양이어도 값, 목록 항목 이름, 달력의 연·월, 토스트처럼 제목이 아닌 요소가 있다.
 * 이런 파일은 `EXPECTED`에 이유와 함께 적는다. 이유 없이 목록에서 빼면 다음 사람이 지워도 되는지 알 수 없다
 * (`check-orphan-routes`와 같은 규칙).
 *
 * 두 번째 검사는 공용 컴포넌트가 구조를 출력하는지 확인한다. 첫 번째 검사는 호출하는 쪽만 보는데,
 * 파급이 큰 쪽은 컴포넌트다(`SectionHeader` 하나가 `TableCard`를 거쳐 여러 표 화면에 쓰인다).
 * 그래서 컴포넌트가 heading을 출력하는지, 대화상자가 그 제목을 `aria-labelledby`로 가리키는지 따로 확인한다
 * (`role="dialog"`는 내용에서 이름을 얻지 못한다).
 *
 * 세 번째 검사는 페이지에 `h1`이 정확히 하나인지 확인한다. `h1`은 화면의 이름이라,
 * 두 개면 개요가 흐려지고 없으면 개요가 이름 없이 시작한다.
 * 인증 화면의 마케팅 문구를 `h1`으로 쓰면 두 개가 되고, 워크벤치가 제목을 빼면 없게 된다.
 *
 * 한 페이지의 `h1` 수는 라우트 조합에 따라 달라 정적으로 셀 수 없으므로 `h1` 공급원의 수를 센다.
 * 공급원은 그 파일 자신의 `<h1`과, `h1`을 내는 컴포넌트(`PageHeader`·`PersonalHeader`·`WorkbenchHeader` …) 두 가지다.
 * 새 공급원이 검사에서 빠지지 않도록 공급원 목록은 하드코딩하지 않고 `components/` 안에서 `<h1`을 내는 파일의 export 이름으로 찾는다.
 *
 * 주의: 한 파일에 화면이 여럿이면 이 셈이 틀린다. `pipeline`은 서로 배타적인 두 분기가 각각 `h1`을 낸다.
 * 이런 파일은 `H1_EXPECTED`에 화면에서 직접 센 값과 함께 적는다.
 *
 * CI는 `--strict`로 실행한다. 새 위반은 생길 때 막는 편이 나중에 전 화면을 다시 훑는 것보다 비용이 적고,
 * 제목이 아닌 요소는 `EXPECTED`에 사유와 함께 등록해 예외로 둘 수 있다.
 *
 * 사용법:  node scripts/check-headings.mjs            (훑을 때. 공용 컴포넌트 규칙만 exit 1)
 *          node scripts/check-headings.mjs --strict   (목록에 없는 항목이 있으면 exit 1, CI용)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

/** 제목 모양이지만 제목이 아닌 요소가 있는 파일 목록. 파일별로 이유를 적는다. */
const EXPECTED = {
  "features/insights/components/ActionNeeded.tsx": "조치 필요 목록 항목의 이름 — 목록이라 항목마다 heading 을 두지 않는다",
  "features/insights/components/ManagerCards.tsx": "진척률 기준 이름과 단계별 대기 칸 이름 — 값의 이름이다",
  "features/insights/components/WorkerCards.tsx": "보유 중·가져올 수 있는 작업 같은 수의 이름(dt) — 값의 이름이다",
  "features/people/components/PersonDrawer.tsx": "실적 칸 이름(dt) — 값의 이름이다",
  "features/people/components/PersonSettings.tsx": "참여 기간 입력 묶음의 legend 와 저장 막대의 상태 줄 — 폼 레이블과 상태 표시다",
  "features/ingest/components/IngestStatusTiles.tsx": "상태 타일 버튼의 이름(요청 대기·분석 중 …) — 누르면 상태별 보기로 가는 버튼 글자다",
  "features/ingest/components/UploadQueue.tsx": "끌어 놓는 칸의 이름(영상 파일을 끌어 놓기) — 누르는 영역의 글자다",
  "features/pipeline/components/ExportPanel.tsx": "요약 칸 링크의 이름(납품 불가·규칙만 검사) — 누르면 완료 표의 축이 열리는 링크 글자다. 카드 이름은 h3이 맡는다",
  "features/projects/components/AnalysisStackCard.tsx": "고르는 항목의 이름(라디오 라벨) — 제목이면 개요에 선택지가 들어간다",
  "shared/ui/SettingRow.tsx": "설정 한 줄의 이름 — 입력란의 label이거나 스위치가 aria-labelledby로 가리키는 이름이다. 행마다 제목이면 개요에 설정 항목이 모두 들어간다",
  "features/projects/pages/project_settings.tsx": "설정 메뉴의 묶음 이름과 저장 막대의 상태 줄 — 메뉴 라벨과 상태 표시다. 페이지 절 제목은 pageHead의 h2가 맡는다",
  "shared/ui/SectionHeader.tsx": "컴포넌트 자신 — 태그는 `as` prop 이 정한다(아래 두 번째 검사가 본다)",
  "shared/ui/ShortcutsModal.tsx": "단축키 묶음 라벨(대문자 캡션)",
  "shared/ui/Toast.tsx": "토스트 제목 — 페이지 개요에 들어가면 안 된다(잠깐 떴다 사라진다)",
  "features/platform/pages/connections.tsx": "경고 문구(되살릴 수 없습니다) — 제목이 아니다",
  "features/projects/pages/projects.tsx": "프로젝트 카드의 이름 — 목록 항목이라 항목마다 heading 을 두지 않는다",
  "features/library/pages/video_clips.tsx": "선택된 클립 수",
  "shell/AppSidebar.tsx": "계정 메뉴 버튼 안의 이름 — 버튼 내용이다",
  "shared/ui/ErrorState.tsx": "실패 안내의 머리글 — role=alert 안의 상태 문장이라 개요에 넣지 않는다(EmptyState 와 같다)",
  "app/_layout.tsx": "세션 만료 창의 결과 문장과 계정 필드 이름 — 창 제목은 Modal의 h2가 맡는다",
  "features/platform/components/DiagnosticsList.tsx": "진단 목록 항목의 판정 낱말과 항목 이름 — 목록이라 항목마다 heading 을 두지 않는다",
  "features/platform/pages/admin_global.tsx": "조치 카드의 수(1건)와 목록 항목 이름(문제 연결) — 값과 목록 항목이다. 절 제목은 h2·h3가 맡는다",
  "features/platform/pages/admin_users.tsx": "확인 창의 굵은 결과 문장과 역할 비교 칸의 값, 활동 이력 표 머리 — 창 제목은 Modal의 h2가 맡는다",
  // ── 워크벤치. 창·서랍의 제목은 Modal·Drawer의 h2가 맡는다. ──
  "features/workbench/components/WorkbenchConfirm.tsx": "확인 창의 굵은 결과 문장 — 창 제목은 Modal의 h2가 맡는다",
  "features/workbench/components/ReportDeidentModal.tsx": "신고 서랍의 굵은 결과 문장과 입력란 이름 — 서랍 제목은 Drawer의 h2가 맡는다",
  "features/workbench/components/WorkbenchHeader.tsx": "머리 줄의 영상 제목 — 화면 이름은 같은 머리의 sr-only h1이 맡는다",
  "features/workbench/components/LabelWorkbench.tsx": "읽기 전용·잠김 띠의 굵은 결과 문장 — 상태 표시다",
  "features/workbench/components/GuidePanel.tsx": "가이드가 비었을 때의 빈 상태 머리글 — 서랍 제목은 h2가 맡는다(EmptyState 와 같다)",
  "features/pipeline/components/CompletedClipView.tsx": "이력 시간선 항목의 이름(AI 초안) — 목록 항목마다 heading 을 두지 않는다",
  "features/pipeline/pages/review.tsx": "판정 영역의 읽기 전용 상태 문장 — 상태 표시다",
  "features/me/pages/account.tsx": "설정 한 줄의 이름(입력란 label)·비밀번호 카드의 결과 문장·표 머리·동의 철회 창의 결과 문장 — 절 제목은 카드의 h2가, 창 제목은 Modal의 h2가 맡는다",
  "features/me/pages/inbox.tsx": "프로젝트 나가기 창의 결과 문장 — 창 제목은 Modal의 h2가 맡는다",
  "features/projects/pages/project_new.tsx": "되돌릴 수 없다는 잠금 줄과 분석 방식 선택지의 기본 표시 — 제목이 아니다",
  "shell/ProjectSwitcher.tsx": "전환기 목록 항목의 프로젝트 이름 — 목록이라 항목마다 heading 을 두지 않는다",
  "features/workbench/pages/completed_workbench.tsx": "완료 목록 행 버튼 안의 영상 제목 — 목록 항목이라 행마다 heading 을 두지 않는다",
  "features/pipeline/components/NextWorkCard.tsx": "보유 작업 미리보기의 이름표(보유 중인 작업) — 카드 제목은 h3이고 이 줄은 본문 한 칸의 이름표다",
};

/** 공용 컴포넌트가 반드시 출력해야 하는 구조. 각 문자열이 해당 파일에 있어야 한다. */
const COMPONENT_RULES = [
  ["shared/ui/PageHeader.tsx", ["<h1"], "페이지 제목은 h1 하나다"],
  ["shared/ui/SectionHeader.tsx", ["<Heading", 'as: Heading = "h2"'], "절 제목은 heading 으로 낸다"],
  ["shared/ui/Modal.tsx", ["aria-labelledby", "<h2"], "대화상자는 제목을 이름으로 쓴다"],
  ["shared/ui/Drawer.tsx", ["aria-labelledby", "<h2"], "대화상자는 제목을 이름으로 쓴다"],
];

/**
 * `h1` 공급원이 하나가 아니어도 되는 파일 목록. 화면에서 센 값과 함께 적는다.
 *
 * 화면에서 직접 세어 본 파일만 적는다. 짐작으로 넣으면 실제 버그가 이 목록에 가려진다.
 */
const H1_EXPECTED = {};

// 절 제목의 모양: className에 `font-semibold`, 타입 크기(SCALE), 글자색(INK)이 함께 있다.
const TAG = /<(\w+)[^>]*?className=\{?"([^"]*font-semibold[^"]*)"/g;
const SCALE = /\btext-(callout|caption|footnote|subhead|body|headline)\b/;
const INK = /\btext-label(-[234])?\b/;

/** 디렉터리를 재귀로 돌며 `.tsx` 파일 경로를 모은다. */
function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (e.endsWith(".tsx")) out.push(p);
  }
  return out;
}

const offenders = [];
for (const file of walk(SRC)) {
  const rel = file.slice(SRC.length + 1);
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    for (const m of line.matchAll(TAG)) {
      const [, tag, cls] = m;
      if (!SCALE.test(cls) || !INK.test(cls)) continue;
      if (/^h[1-6]$/.test(tag)) continue;
      offenders.push({ rel, line: i + 1, tag, text: line.trim().slice(0, 88) });
    }
  });
}

/* ── 세 번째 검사: `h1` 공급원 수 ─────────────────────────────────────────── */

/** 주석을 지운다. 주석은 근거를 적는 곳이라 `<h1`이 예시로 들어 있을 수 있다. */
const stripComments = (src) =>
  src
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, " ")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ");

/**
 * 부품 폴더이면 true. 공용 부품(`shared/`), 셸(`shell/`), 도메인 부품(`components/`, `features/<d>/components/`)이다.
 * 한 곳이라도 빠지면 그 폴더의 `h1` 공급원을 세지 못해 그 부품을 쓰는 화면이 이름 없는 화면으로 잡힌다.
 */
const isComponentFile = (rel) =>
  /^(components|shared|shell)\//.test(rel) || /^features\/[^/]+\/components\//.test(rel);

/** 화면 파일이면 true. 페이지는 `routes/`와 `features/<d>/pages/`에 두고, 라우트 표와 셸(`app/`)은 화면에서 뺀다. */
const isPageFile = (rel) => rel.startsWith("routes/") || /^features\/[^/]+\/pages\//.test(rel);

/** 부품 폴더에서 `h1`을 내는 컴포넌트 이름 목록을 모은다. 하드코딩하지 않고 파일에서 찾는다. */
const h1Emitters = [];
for (const file of walk(SRC)) {
  const rel = file.slice(SRC.length + 1);
  if (!isComponentFile(rel)) continue;
  const src = stripComments(readFileSync(file, "utf8"));
  if (!/<h1[\s>]/.test(src)) continue;
  for (const m of src.matchAll(/export function ([A-Z]\w+)/g)) h1Emitters.push(m[1]);
}

/**
 * 화면이 아닌 라우트 파일이면 true. 셸, 라우트 표, 리다이렉트, 다른 경로로 넘기는 파일이다.
 *
 * 파일 이름 목록 대신 구조로 판정한다. 이름을 적으면 새 파일이 생길 때마다 손을 대야 하고, 왜 빠졌는지가 남지 않는다.
 * - `<Outlet`: 셸(`_layout`)이다. 이름은 그 안의 페이지가 준다.
 * - `createBrowserRouter`: 라우트 표(`router`)다.
 * - HTML 태그가 하나도 없음: 넘기기·리다이렉트만 하는 파일이다(`label_route`·`redirects`·`home`).
 *
 * 주의: 드러나지 않는 누락은 오탐보다 찾기 어려우므로 `<Navigate` 하나만으로 건너뛰지 않는다.
 * 그러면 페이지 여럿에 리다이렉트 하나가 섞인 파일(`people`)이 통째로 빠진다.
 * 그래서 리다이렉트만 있는 파일(HTML 태그 없음)일 때만 건너뛴다.
 */
const notAScreen = (src) =>
  /<Outlet\b/.test(src) ||
  /createBrowserRouter/.test(src) ||
  !/<[a-z][a-z0-9]*[\s/>]/.test(src);

const h1Bad = [];
for (const file of walk(SRC)) {
  const rel = file.slice(SRC.length + 1);
  if (!isPageFile(rel)) continue;
  const src = stripComments(readFileSync(file, "utf8"));
  if (notAScreen(src)) continue;
  const own = (src.match(/<h1[\s>]/g) ?? []).length;
  const used = h1Emitters.filter((e) => new RegExp("<" + e + "[\\s/>]").test(src));
  const n = own + used.length;
  if (n === 1) continue;
  h1Bad.push({ rel, n, own, used, why: n === 0 ? "이 화면의 이름이 개요에 안 잡힌다" : "둘 이상이 이름을 주장한다" });
}
const h1Unexpected = h1Bad.filter((b) => !(b.rel in H1_EXPECTED));
const h1Stale = Object.keys(H1_EXPECTED).filter((rel) => !h1Bad.some((b) => b.rel === rel));

const unexpected = offenders.filter((o) => !(o.rel in EXPECTED));
const missing = [];
for (const [rel, needles, why] of COMPONENT_RULES) {
  const src = readFileSync(join(SRC, rel), "utf8");
  for (const n of needles) if (!src.includes(n)) missing.push({ rel, n, why });
}
const staleAllow = Object.keys(EXPECTED).filter((rel) => !offenders.some((o) => o.rel === rel));

if (missing.length) {
  console.error("\n❌ 공용 컴포넌트가 구조를 안 낸다 — 여기가 깨지면 화면 수십 곳이 같이 깨진다\n");
  for (const m of missing) console.error(`   ${m.rel} 에 ${JSON.stringify(m.n)} 가 없다 — ${m.why}`);
}
if (unexpected.length) {
  console.error(`\n❌ 절 제목 모양인데 heading 이 아닌 곳 ${unexpected.length}건\n`);
  for (const o of unexpected) console.error(`   ${o.rel}:${o.line}  <${o.tag}>  ${o.text}`);
  console.error("\n   제목이면 h2(절)·h3(절 안의 절)로 바꾸고, 제목이 아니면 EXPECTED 에 이유와 함께 적는다.");
}
if (h1Unexpected.length) {
  console.error(`\n❌ \`h1\` 공급원이 하나가 아닌 라우트 ${h1Unexpected.length}건\n`);
  for (const b of h1Unexpected)
    console.error(`   ${b.rel}  공급원 ${b.n}개 (자체 ${b.own} + ${b.used.join("·") || "없음"}) — ${b.why}`);
  console.error("\n   0이면 이름을 준다 — 보이는 제목이 이미 있으면 `sr-only h1`로 개요에만.");
  console.error("   2 이상이면 페이지 제목 하나만 `h1`로 남기고 나머지는 내린다.");
  console.error("   한 파일에 화면이 여럿이라 헛짚은 것이면, **화면에서 세어 본 뒤** H1_EXPECTED 에 적는다.");
}
if (h1Stale.length) {
  console.log(`\n⚠️  H1_EXPECTED 에 있는데 더는 해당 없는 파일 ${h1Stale.length}건 — 지워도 된다`);
  for (const rel of h1Stale) console.log(`   ${rel}`);
}
if (staleAllow.length) {
  console.log(`\n⚠️  EXPECTED 에 있는데 더는 해당 없는 파일 ${staleAllow.length}건 — 지워도 된다`);
  for (const rel of staleAllow) console.log(`   ${rel}`);
}
if (!missing.length && !unexpected.length && !h1Unexpected.length) {
  console.log(
    `✅ 절 제목은 heading 으로 나간다 · 공용 컴포넌트 ${COMPONENT_RULES.length}종 정상 · ` +
      `제목 아닌 것 ${offenders.length}건 사유 등록 · h1 공급원 1개 (공급 컴포넌트 ${[...new Set(h1Emitters)].join("·")})`,
  );
}

const strict = process.argv.includes("--strict");
process.exit(missing.length || (strict && (unexpected.length || h1Unexpected.length)) ? 1 : 0);
