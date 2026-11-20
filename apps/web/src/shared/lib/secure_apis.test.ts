/**
 * 보안 컨텍스트 전용 API를 대체 경로 파일만 부르는지 검증한다.
 *
 * 운영 화면을 평문 http 주소로 열면 `crypto.randomUUID`·`crypto.subtle`·`navigator.clipboard`가 없다.
 * 직접 부르면 TypeError로 업로드가 시작되지 않고 복사 실패 알림도 뜨지 않는다.
 * localhost는 보안 컨텍스트라 개발 화면에서는 드러나지 않으므로 소스를 읽어 막는다.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = resolve(fileURLToPath(new URL(".", import.meta.url)), "../..");
/** 대체 경로를 가진 함수가 있는 파일. 이 파일만 원래 API를 부른다. */
const ALLOWED = new Set(["shared/lib/uuid.ts", "shared/lib/sha256.ts", "shared/lib/clipboard.ts"]);
const PATTERN = /\bcrypto\.(randomUUID|subtle)\b|\bnavigator\.clipboard\b/;

/** `dir` 아래의 테스트가 아닌 ts·tsx 파일 경로를 모은다. */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

describe("보안 컨텍스트 전용 API", () => {
  it("화면 코드는 randomUuid·sha256·copyText를 거쳐 부른다", () => {
    const hits = sources(SRC)
      .map((p) => relative(SRC, p))
      .filter((rel) => !ALLOWED.has(rel))
      .flatMap((rel) =>
        readFileSync(join(SRC, rel), "utf8").split("\n")
          .map((line, i) => ({ line, at: `${rel}:${i + 1}` }))
          // 주석 줄은 API 이름을 설명으로 담을 수 있으므로 검사에서 뺀다.
          .filter(({ line }) => PATTERN.test(line) && !/^\s*(\/\/|\*|\/\*)/.test(line))
          .map(({ at }) => at));
    expect(hits).toEqual([]);
  });
});
