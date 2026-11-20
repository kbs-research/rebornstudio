/**
 * 캡션 읽기 규칙이 서버와 같은지 확인한다.
 *
 * 판정 표(`packages/reborn-contracts/caption-shapes.json`)를 서버 테스트
 * (`apps/api-gateway/tests/contract/test_caption_shapes.py`)와 함께 읽는다.
 * 한쪽만 실패하면 두 런타임이 같은 캡션을 다르게 읽는다는 뜻이다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  finalText, joinSentences, liveSentences, sentencesOf, toSentences,
} from "./caption";

type Shapes = {
  cases: { why: string; payload: unknown; sentences: unknown[]; text: string }[];
};

const shapes = JSON.parse(
  readFileSync(
    resolve(
      fileURLToPath(new URL(".", import.meta.url)),
      "../../../../../../packages/reborn-contracts/caption-shapes.json",
    ),
    "utf-8",
  ),
) as Shapes;

describe("판정 표가 비어 있지 않다", () => {
  it("비면 아래 시험들이 0건을 통과한다", () => {
    expect(shapes.cases.length).toBeGreaterThanOrEqual(10);
  });
});

describe("sentencesOf — 서버 `sentences_of`와 같은 문장 목록", () => {
  for (const c of shapes.cases) {
    it(c.why, () => {
      expect(sentencesOf(c.payload)).toEqual(c.sentences);
    });
  }
});

describe("finalText — 서버 `final_text`와 같은 문자열", () => {
  for (const c of shapes.cases) {
    it(c.why, () => {
      expect(finalText(c.payload)).toBe(c.text);
    });
  }
});

describe("화면 부품이 쓰는 조합", () => {
  it("문장 배열만 있는 제출도 문장이 있다 — 종전 읽기(`caption.brief`)는 빈 캡션으로 봤다", () => {
    const data = { caption: { brief_sentences: [{ id: "a", text: "앵커가 소식을 전한다." }] } };
    expect(liveSentences(sentencesOf(data)).map((s) => s.text)).toEqual(["앵커가 소식을 전한다."]);
    expect(finalText(data)).toBe("앵커가 소식을 전한다.");
  });

  it("toSentences는 낯선 원소가 섞인 배열에서도 깨지지 않는다", () => {
    const mixed = [null, "문자열", { id: "x", text: "개체입니다." }] as unknown as Parameters<typeof toSentences>[0];
    expect(toSentences(mixed, "무시").map((s) => s.text)).toEqual(["개체입니다."]);
  });

  it("joinSentences는 문장마다 양끝 공백을 걷고 공백 하나로 잇는다", () => {
    expect(joinSentences([{ id: "a", text: " 가. " }, { id: "b", text: "나. " }])).toBe("가. 나.");
  });
});
