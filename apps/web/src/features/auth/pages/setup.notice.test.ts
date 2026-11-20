/**
 * 최초 설정 화면이 지원 기관 표기를 첫 화면 하단에 두는지 확인한다.
 * 미초기화 배포에서는 이 화면이 첫 화면이고 인증 셸을 쓰지 않으므로 표기를 따로 둔다.
 * DOM 테스트 도구가 없어 소스를 읽어 확인한다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = readFileSync(resolve(fileURLToPath(new URL(".", import.meta.url)), "setup.tsx"), "utf8");

describe("최초 설정 화면 지원 기관 표기", () => {
  it("인증 셸의 표기 부품을 그대로 쓴다", () => {
    expect(SRC).toContain('import { SupportNotice } from "../components/AuthShell";');
  });

  it("넓은 폭(왼쪽 패널)과 좁은 폭(폼 아래) 두 자리에 모두 둔다", () => {
    const uses = SRC.match(/<SupportNotice\b[^>]*\/>/g) ?? [];
    expect(uses).toHaveLength(2);
    expect(uses.filter((u) => u.includes("lg:hidden"))).toHaveLength(1);
  });

  it("셋업 토큰 명령에 특정 서버 경로나 컨테이너 이름을 쓰지 않는다", () => {
    expect(SRC).not.toContain("/opt/rebornstudio");
    expect(SRC).not.toContain("local-api-1");
  });
});
