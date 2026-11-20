/**
 * 다음 작업 카드의 대기 수와 버튼 낱말 검사(`QUEUE_SPEC`, `queueCountLabels`).
 */
import { describe, expect, it } from "vitest";

import { QUEUE_SPEC, queueCountLabels } from "./NextWorkCard";

/* 큐의 대기 수는 큐마다 세는 대상이 다르다.
   라벨링의 대기 수는 누구나 받을 수 있는 수라서 `대기`라고 쓴다. 검수는 서버가 호출한 사용자가 라벨링하지 않은 작업만 센다.
   그래서 단계 배지 `검수 14`와 큐의 `받을 수 있는 검수 작업 0건`이 함께 뜰 수 있다.
   두 수는 모두 맞지만, 같은 낱말로 쓰면 사용자는 작업이 있는데 없다고 표시된다고 여긴다. */
describe("대기 수의 분모", () => {
  it("검수만 사람 범위 낱말을 쓴다", () => {
    expect(QUEUE_SPEC.review.poolWord).toBe("받을 수 있는");
    expect(QUEUE_SPEC.label.poolWord).toBe("대기");
  });

  it("검수만 무엇이 빠졌는지를 말한다 — 낱말만 바꾸면 왜인지가 없다", () => {
    expect(QUEUE_SPEC.review.poolNote).toContain("라벨링한 작업");
    // 메모 말투(`남이 쥔 건`)를 쓰지 않는다.
    expect(QUEUE_SPEC.review.poolNote).not.toMatch(/쥔|남이/);
    expect(QUEUE_SPEC.label.poolNote).toBeUndefined();
  });

  it("사연이 있는 큐는 사람 범위 낱말도 같이 쓴다 — 둘이 갈리면 또 어긋난다", () => {
    for (const spec of Object.values(QUEUE_SPEC)) {
      if (spec.poolNote) expect(spec.poolWord).not.toBe("대기");
    }
  });
});

describe("대기 수 칩", () => {
  it("칩은 수를 줄이지 않는다 — 99+는 좁은 배지에만 쓴다", () => {
    // 스텝퍼 배지와 칩의 표기가 다르면 다른 수처럼 읽힌다.
    expect(queueCountLabels("label", 147)).toEqual(["대기 작업 147건"]);
    expect(queueCountLabels("label", 1234)).toEqual(["대기 작업 1,234건"]);
  });

  it("작업은 건으로 센다(용어 사전)", () => {
    expect(queueCountLabels("review", 0)).toEqual(["받을 수 있는 검수 작업 0건"]);
  });

  it("모르는 수는 안 그린다 — 조회 전·실패를 0으로 쓰면 «할 일 없음»이 된다", () => {
    expect(queueCountLabels("label", undefined)).toEqual([]);
    expect(queueCountLabels("label", null)).toEqual([]);
  });
});

describe("주 버튼 이름", () => {
  it("역할마다 버튼 하나이고 이름은 명사구다", () => {
    expect(QUEUE_SPEC.label.nextLabel).toBe("다음 작업");
    expect(QUEUE_SPEC.review.nextLabel).toBe("다음 검수");
  });
});
