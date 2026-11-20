/**
 * 캡션 토큰 수 검사. 화면의 계산이 서버와 같은 값인지 확인한다.
 *
 * 서버도 제출 조건을 검사하므로(`services/caption.py`) 두 구현이 다르면 버튼은 활성인데 제출이 거절된다.
 * 런타임이 달라 함수를 합칠 수 없으므로 판정 표(`packages/reborn-contracts/caption-gate.json`)를 양쪽 테스트가 읽는다.
 * 서버 쪽 짝은 `apps/api-gateway/tests/contract/test_caption_gate.py`다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { finalText } from "./caption";
import { estimateTokens } from "./tokens";

type Vectors = {
  tokens: { text: string; expected: number; why: string }[];
  payloads: {
    payload: { caption?: unknown };
    text: string;
    why: string;
  }[];
};

const vectors = JSON.parse(
  readFileSync(
    resolve(
      fileURLToPath(new URL(".", import.meta.url)),
      "../../../../../../packages/reborn-contracts/caption-gate.json",
    ),
    "utf-8",
  ),
) as Vectors;

/*
 * 화면이 payload에서 최종 캡션을 얻는 곳은 `lib/caption`의 `finalText` 하나다.
 * 세 모양의 판정은 `caption.test.ts`가 본다. 이 파일에 같은 규칙을 따로 적지 않는다.
 */

describe("판정 표가 비어 있지 않다", () => {
  it("비면 아래 시험들이 0건을 통과한다 — 초록불이 거짓이 되는 자리다", () => {
    expect(vectors.tokens.length).toBeGreaterThanOrEqual(10);
    expect(vectors.payloads.length).toBeGreaterThanOrEqual(8);
  });
});

describe("estimateTokens — 서버(`services/caption.py`)와 같은 수", () => {
  for (const c of vectors.tokens) {
    it(`${c.why} — ${JSON.stringify(c.text).slice(0, 36)}`, () => {
      expect(estimateTokens(c.text)).toBe(c.expected);
    });
  }
});

describe("payload에서 꺼낸 최종 캡션 — 서버와 같은 문자열", () => {
  for (const c of vectors.payloads) {
    it(c.why, () => {
      expect(finalText(c.payload)).toBe(c.text);
    });
  }
});
