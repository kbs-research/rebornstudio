/**
 * 보조 글자 토큰의 대비 테스트.
 *
 * `text-label-3`은 12~13px 본문(감사 로그 시각·이메일, 단계 머리 부제 등)이 쓴다.
 * 종이·흑연 두 테마의 네 면 모두에서 WCAG 본문 기준 4.5:1을 넘어야 한다. 값은 `tokens.css`에서 읽는다.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(path.resolve(__dirname, "../tokens.css"), "utf8");

/** 블록 하나에서 `--이름: #RRGGBB` 값을 모은다. 키는 `--`를 붙인 변수 이름 그대로다. */
function vars(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;/g)) out[m[1]] = m[2];
  return out;
}

/** 상대 휘도(WCAG 2.x). */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 두 색의 대비. */
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const graphiteStart = css.indexOf("@media not print");
const paper = vars(css.slice(0, graphiteStart));
const graphite = vars(css.slice(graphiteStart));
const SURFACES = ["--bg", "--bg-secondary", "--surface-1", "--surface-2"];

describe.each([
  ["종이", paper],
  ["흑연", graphite],
])("%s 판의 보조 글자", (_name, t) => {
  it.each(SURFACES)("label-3은 %s 면에서 4.5:1을 넘는다", (surface) => {
    expect(contrast(t["--label-tertiary"], t[surface])).toBeGreaterThanOrEqual(4.5);
  });
  it("label-3은 label-2보다 흐리다(위계를 지킨다)", () => {
    expect(contrast(t["--label-tertiary"], t["--surface-1"])).toBeLessThan(contrast(t["--label-secondary"], t["--surface-1"]));
  });
});
