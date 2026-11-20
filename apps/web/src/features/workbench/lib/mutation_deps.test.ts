/**
 * 훅 의존성 목록에 `useMutation` 결과 객체를 넣지 않는지 검사한다.
 *
 * `useMutation`은 렌더마다 새 객체를 돌려준다(`mutate`만 참조가 안정적이다).
 * 그 객체를 `useCallback`·`useEffect` 의존성에 넣으면 렌더마다 콜백이 바뀌고 이펙트가 다시 실행된다.
 * 매초 다시 그리는 남은 시간 표시가 있는 화면에서는 30초 자동 저장 타이머가 매초 지워져 한 번도 실행되지 않는다.
 * `tsc`도 빌드도 이 실수를 잡지 못해서 소스를 읽어 검사한다.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = resolve(fileURLToPath(new URL(".", import.meta.url)), "../../..");

/** 테스트를 뺀 `src` 아래 TypeScript 파일 경로 목록을 구한다. */
function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sourceFiles(p));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

/** 한 파일에서 의존성 목록에 들어간 `useMutation` 결과 이름을 `파일:줄: 이름`으로 구한다. */
function mutationObjectDeps(path: string, text: string): string[] {
  const names = new Set([...text.matchAll(/const\s+(\w+)\s*=\s*useMutation\b/g)].map((m) => m[1]));
  if (names.size === 0) return [];
  const hits: string[] = [];
  for (const m of text.matchAll(/\},\s*\[([^\]]*)\]\s*\)/g)) {
    for (const dep of m[1].split(",").map((d) => d.trim())) {
      if (names.has(dep)) {
        const line = text.slice(0, m.index).split("\n").length;
        hits.push(`${path}:${line}: ${dep}`);
      }
    }
  }
  return hits;
}

describe("useMutation 결과 객체를 훅 의존성에 넣지 않는다", () => {
  it("의존성 목록에 결과 객체가 들어간 줄을 찾아낸다", () => {
    const text = [
      "const draftMut = useMutation({});",
      "const save = useCallback(() => {",
      "  draftMut.mutate();",
      "}, [id, draftMut]);",
      "const ok = useCallback(() => {",
      "  mutate();",
      "}, [id, mutate]);",
    ].join("\n");
    expect(mutationObjectDeps("x.ts", text)).toEqual(["x.ts:4: draftMut"]);
  });

  it("src 전체에 그런 줄이 없다", () => {
    const hits = sourceFiles(SRC).flatMap((p) => mutationObjectDeps(relative(SRC, p), readFileSync(p, "utf-8")));
    expect(hits).toEqual([]);
  });
});
