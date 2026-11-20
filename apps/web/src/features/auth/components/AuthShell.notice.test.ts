/**
 * 인증 화면 셸이 지원 기관 표기를 첫 화면 하단에 두는지 확인한다.
 * 지원 사업 협약이 정한 문구라 화면 정리 중에 빠지면 안 된다. DOM 테스트 도구가 없어 소스를 읽어 확인한다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = readFileSync(resolve(fileURLToPath(new URL(".", import.meta.url)), "AuthShell.tsx"), "utf8");

describe("AuthShell 지원 기관 표기", () => {
  it("개발지원 표기와 재단 로고를 싣고, 로고 대체 문장은 협약 문구다", () => {
    expect(SRC).toContain("개발지원");
    expect(SRC).toContain('import kpfLogo from "./kpf-logo.png"');
    expect(SRC).toContain("본 시스템은 한국언론진흥재단의 지원을 받아 개발되었습니다.");
    expect(SRC).toMatch(/<img src=\{kpfLogo\} alt=\{SUPPORT_NOTICE\}/);
  });

  it("넓은 폭(왼쪽 판)과 좁은 폭(폼 아래) 두 자리에 모두 둔다", () => {
    expect(SRC.match(/<SupportNotice\b/g)?.length).toBe(2);
  });
});
