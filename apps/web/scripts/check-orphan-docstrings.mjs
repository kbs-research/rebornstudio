/**
 * 독스트링이 다른 함수 위에 잘못 남아 있는지 검사한다.
 *
 * 이 저장소의 독스트링은 그렇게 구현한 이유를 담아서 길고 중요하다. 코드가 다른 곳으로
 * 옮겨 가면 설명만 원래 위치에 남는 경우가 있다. 그러면 실제 대상 함수는 설명이 없는 것처럼
 * 보이고, 관계없는 함수 위에는 다른 함수의 설명이 붙는다.
 *
 * 판정: 독스트링 두 개가 사이에 코드 없이 연달아 있으면 뒤의 독스트링이 바로 아래 선언을 설명하므로,
 * 앞의 독스트링은 설명할 코드가 없다. 독스트링은 `/**`로 시작하는 주석만 세고, 일반 주석(`/*`)과 독스트링의 쌍은 정상으로 본다.
 *
 * 예외는 모듈 헤더 하나다. 파일의 첫 독스트링은 파일 전체를 설명하고 그다음 독스트링이 첫 선언을 설명하므로 정상으로 본다.
 *
 * 한계: 코드만 바뀌고 설명을 고치지 않은 독스트링은 연달아 붙지 않으므로 잡지 못하고, 사람이 읽어서 찾는다.
 * 이 검사는 코드가 옮겨 가고 남은 설명만 잡는다.
 *
 * 사용법:  node scripts/check-orphan-docstrings.mjs
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const WEB = new URL("../src", import.meta.url).pathname;

/** 디렉터리를 재귀로 돌며 이름이 `re`에 맞는 파일 경로를 모은다. */
function walk(dir, re, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, re, out);
    else if (re.test(name)) out.push(p);
  }
  return out;
}

const fails = [];
for (const f of walk(WEB, /\.tsx?$/)) {
  const lines = readFileSync(f, "utf8").split("\n");
  /* 모듈 헤더는 파일의 첫 독스트링이다. 헤더와 그 뒤 첫 독스트링의 쌍은 정상이라 뺀다.
     `SentenceEditor.tsx`처럼 헤더가 import 아래에 있는 파일도 있어서 헤더는 위치 대신 순서로 정의한다.
     한계: 헤더가 없는 파일에서 첫 두 독스트링이 붙어 있으면 놓친다. */
  let headerEnd = -1;
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].trim().startsWith("/**")) continue;
    headerEnd = i;
    while (headerEnd < lines.length && lines[headerEnd].trim() !== "*/") headerEnd++;
    break;
  }

  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() !== "*/") continue;
    let j = i + 1;
    while (j < lines.length && !lines[j].trim()) j++;
    if (j >= lines.length || !lines[j].trim().startsWith("/**")) continue;
    if (i === headerEnd) continue;   // 모듈 헤더 다음의 첫 선언 독스트링이라 정상이다.
    /* 닫는 줄이 속한 주석의 시작 줄을 찾고, `/**`로 시작한 주석만 독스트링으로 센다.
       일반 주석(`/*`) 뒤에 독스트링이 오는 경우는 설명 주석 아래에 선언이 오는 정상 형태다.
       시작 줄을 `/**`로만 찾으면 일반 주석 뒤의 독스트링을 한 쌍으로 세어 오탐이 난다. */
    let s = i;
    while (s >= 0 && !lines[s].includes("/*")) s--;
    if (s < 0 || !lines[s].trim().startsWith("/**")) continue;
    const gist = (lines[s + 1] ?? "").replace(/^\s*\*\s?/, "").trim().slice(0, 60);
    fails.push({ file: f.replace(WEB, "src"), line: s + 1, gist });
  }
}

if (fails.length) {
  console.error(`❌ 주인 없는 독스트링 ${fails.length}건:\n`);
  for (const x of fails) console.error(`  ${x.file}:${x.line}\n      ${x.gist}`);
  console.error(`
  독스트링 둘이 사이에 코드 없이 붙어 있습니다. 앞엣것이 설명하는 함수를 찾아
  그 위로 옮기거나, 그 함수가 다른 파일로 갔으면 남은 설명을 지웁니다.`);
  process.exit(1);
}
console.log("✅ 주인 없는 독스트링 없음 (모듈 헤더 → 첫 선언 쌍은 정상으로 본다)");
