/**
 * `formatUpdatedAt`이 값이 없는 시각을 `—`로 표시하는지 검증한다.
 *
 * 부르는 쪽이 둘 다 아직 없는 상태를 `Math.min(a || Infinity, b || Infinity)`로 표현할 수 있다.
 * 받는 쪽 조건이 truthy 검사뿐이면 `Infinity`가 통과하고, `new Date(Infinity)`가 `Invalid Date`라는 글자를 만든다.
 *
 * `tsc`와 `vite build`는 둘 다 값을 `number` 타입으로만 보므로 이 문제를 잡아내지 못한다.
 * 화면을 열어야 보이므로 규칙을 순수 함수로 분리해 이 파일의 테스트로 보장한다.
 */
import { describe, expect, it } from "vitest";

import { formatUpdatedAt } from "./LastUpdated";

describe("formatUpdatedAt", () => {
  it("값이 있으면 HH:MM으로 적는다", () => {
    // 로컬 시간대의 영향을 받지 않도록 같은 시각을 같은 규칙으로 만들어 비교한다.
    const at = new Date("2026-09-23T04:05:06Z").getTime();
    const expected = new Date(at).toLocaleTimeString("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
    expect(formatUpdatedAt(at)).toBe(expected);
    expect(formatUpdatedAt(at)).toMatch(/^\d{2}:\d{2}$/);
  });

  it("모르는 값은 전부 «—»다 — 화면에 Invalid Date가 나가지 않는다", () => {
    // 영상 등록 화면 머리에 `마지막 업데이트 Invalid Date`로 표시되는 값이다.
    expect(formatUpdatedAt(Infinity)).toBe("—");

    expect(formatUpdatedAt(undefined)).toBe("—");
    expect(formatUpdatedAt(0)).toBe("—"); // react-query가 아직 데이터를 받지 않았을 때의 값.
    expect(formatUpdatedAt(NaN)).toBe("—");
    expect(formatUpdatedAt(-Infinity)).toBe("—");
    expect(formatUpdatedAt(-1)).toBe("—");
  });
});
