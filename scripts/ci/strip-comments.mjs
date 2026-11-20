/**
 * TS·JS 소스에서 주석을 뺀 출력과 주석 목록을 만든다. `check-comment-only.py`가 부른다.
 *
 * 입력(stdin): `[{"key": ..., "path": ..., "text": ...}]` JSON 배열.
 * 출력(stdout): `{key: {"code": 주석을 뺀 출력, "comments": [주석 원문...]}}` JSON.
 *
 * 비교에 쓰는 출력이라 서식을 정규화한다.
 * - TypeScript 프린터로 다시 찍는다(`removeComments`). 공백·줄바꿈 차이는 사라진다.
 * - JSX 안의 빈 식(`{/* 주석 *\/}`)은 뺀다. 주석을 통째로 지운 것과 같게 본다.
 * - 공백뿐인 JSX 텍스트는 빼고, 나머지 JSX 텍스트는 공백을 하나로 줄인다.
 *
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

/** JSX 자식에서 비교에 의미 없는 것(빈 식, 공백 텍스트)을 빼고 텍스트 공백을 줄인다. */
function normalizeJsx(context) {
  const f = context.factory;
  const cleanChildren = (children) =>
    f.createNodeArray(
      children
        .filter((c) => !(ts.isJsxExpression(c) && !c.expression))
        .filter((c) => !(ts.isJsxText(c) && c.text.trim() === ""))
        .map((c) => (ts.isJsxText(c) ? f.createJsxText(c.text.replace(/\s+/g, " ").trim()) : c)),
    );
  const visit = (node) => {
    if (ts.isJsxElement(node)) {
      node = f.updateJsxElement(node, node.openingElement, cleanChildren(node.children), node.closingElement);
    } else if (ts.isJsxFragment(node)) {
      node = f.updateJsxFragment(node, node.openingFragment, cleanChildren(node.children), node.closingFragment);
    }
    return ts.visitEachChild(node, visit, context);
  };
  return (root) => ts.visitNode(root, visit);
}

/** 소스 전체의 주석 원문을 모은다. 토큰마다 앞뒤 주석 범위를 보고 위치로 중복을 없앤다. */
function collectComments(sf) {
  const text = sf.getFullText();
  const seen = new Map();
  const add = (ranges) => {
    for (const r of ranges ?? []) if (!seen.has(r.pos)) seen.set(r.pos, text.slice(r.pos, r.end));
  };
  const walk = (node) => {
    add(ts.getLeadingCommentRanges(text, node.getFullStart()));
    add(ts.getTrailingCommentRanges(text, node.getEnd()));
    for (const child of node.getChildren(sf)) walk(child);
  };
  walk(sf);
  return [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => c);
}

const items = JSON.parse(readFileSync(0, "utf8"));
const printer = ts.createPrinter({ removeComments: true });
const out = {};
for (const { key, path, text } of items) {
  const sf = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, scriptKind(path));
  const result = ts.transform(sf, [normalizeJsx]);
  out[key] = { code: printer.printFile(result.transformed[0]), comments: collectComments(sf) };
  result.dispose();
}
process.stdout.write(JSON.stringify(out));
