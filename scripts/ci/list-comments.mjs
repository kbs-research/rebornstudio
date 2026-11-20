/**
 * TS·JS 소스의 주석을 줄 번호와 함께 뽑는다. `check-comment-style.py`가 부른다.
 *
 * 입력(stdin): 저장소 루트 기준 파일 경로 JSON 배열.
 * 출력(stdout): `{경로: [[시작 줄, 끝 줄, 주석 원문], ...]}` JSON.
 *
 * 정규식으로 주석을 찾으면 문자열·템플릿 리터럴·JSX 텍스트 속 `//`(URL 등)까지 주석으로 센다.
 * 그래서 TypeScript 파서가 노드마다 돌려주는 앞뒤 주석 범위만 쓴다(`strip-comments.mjs`와 같은 방법).
 * TypeScript 모듈 위치는 환경 변수 `TS_PATH`로 받는다(작업 트리마다 `node_modules` 위치가 다를 수 있다).
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require(process.env.TS_PATH);

/** 파일 확장자로 파서 종류를 고른다. */
function scriptKind(path) {
  if (path.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (path.endsWith(".ts")) return ts.ScriptKind.TS;
  if (path.endsWith(".jsx")) return ts.ScriptKind.JSX;
  return ts.ScriptKind.JS;
}

/**
 * 소스 하나의 주석을 위치순으로 모은다. 같은 주석은 시작 위치로 한 번만 센다.
 *
 * JSX 텍스트 안의 `//`는 화면에 그대로 나가는 글자인데, 주석 범위 함수는 그 위치에서도
 * 주석을 돌려준다. 그래서 JSX 텍스트 노드 범위 안에서 시작하는 주석은 뺀다.
 */
function collect(sf) {
  const text = sf.getFullText();
  const seen = new Map();
  const jsxText = [];
  const add = (ranges) => {
    for (const r of ranges ?? []) if (!seen.has(r.pos)) seen.set(r.pos, r);
  };
  const walk = (node) => {
    if (node.kind === ts.SyntaxKind.JsxText) jsxText.push([node.pos, node.end]);
    add(ts.getLeadingCommentRanges(text, node.getFullStart()));
    add(ts.getTrailingCommentRanges(text, node.getEnd()));
    for (const child of node.getChildren(sf)) walk(child);
  };
  walk(sf);
  const inJsxText = (r) => jsxText.some(([a, b]) => a <= r.pos && r.pos < b);
  return [...seen.values()]
    .filter((r) => !inJsxText(r))
    .sort((a, b) => a.pos - b.pos)
    .map((r) => [
      sf.getLineAndCharacterOfPosition(r.pos).line + 1,
      sf.getLineAndCharacterOfPosition(r.end).line + 1,
      text.slice(r.pos, r.end),
    ]);
}

const paths = JSON.parse(readFileSync(0, "utf8"));
const out = {};
for (const path of paths) {
  const text = readFileSync(path, "utf8");
  const sf = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, scriptKind(path));
  out[path] = collect(sf);
}
process.stdout.write(JSON.stringify(out));
