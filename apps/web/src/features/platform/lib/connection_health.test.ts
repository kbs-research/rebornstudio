/** 플랫폼 개요의 자격증명 연결 한 줄이 쓰지 않는 연결의 실패로 경보를 내지 않는지 확인한다. */
import { describe, expect, it } from "vitest";

import { connectionHealthLine, type ConnectionHealth } from "./connection_health";

/** 빈 값에 `p`를 덮어써 테스트용 연결 상태 요약을 만든다. */
const h = (p: Partial<ConnectionHealth>): ConnectionHealth => ({
  total: 0, ok: 0, warn: 0, fail: 0, unchecked: 0, unused: 0, ...p,
});

describe("connectionHealthLine", () => {
  it("쓰는 곳이 없는 연결의 실패는 빨간 경보가 아니다", () => {
    // 서버는 쓰는 곳이 없는 연결의 실패를 fail 대신 unused로 센다.
    const line = connectionHealthLine(h({ total: 2, ok: 1, unused: 1 }));
    expect(line.tone).toBe("muted");
    expect(line.text).toBe("사용 중 1개 정상 · 사용처 없음 1개");
  });

  it("쓰는 연결이 실패하면 실패를 말하고 빨간색이다", () => {
    const line = connectionHealthLine(h({ total: 3, ok: 1, fail: 1, unused: 1 }));
    expect(line.tone).toBe("bad");
    expect(line.text).toBe("사용 중 2개(실패 1개) · 사용처 없음 1개");
  });

  it("문제가 여럿이면 괄호 안에 쉼표로 잇는다", () => {
    const line = connectionHealthLine(h({ total: 3, fail: 1, warn: 1, unchecked: 1 }));
    expect(line.text).toBe("사용 중 3개(실패 1개, 주의 1개, 점검 전 1개)");
  });

  it("주의는 주의색이고 점검 전은 색을 올리지 않는다", () => {
    expect(connectionHealthLine(h({ total: 2, warn: 1, unchecked: 1 })).tone).toBe("warn");
    expect(connectionHealthLine(h({ total: 1, unchecked: 1 })).tone).toBe("muted");
  });

  it("모두 쓰는 곳이 없으면 그 수만 말한다", () => {
    expect(connectionHealthLine(h({ total: 2, unused: 2 })).text).toBe("사용처 없음 2개");
  });

  it("연결이 없으면 없다고 말한다", () => {
    expect(connectionHealthLine(h({})).text).toBe("연결 없음");
  });
});
