/** 이벤트 사전의 심각도·이름과 감사 시각 형식을 검사한다. */
import { describe, expect, it } from "vitest";

import { eventTone, EVENTS, fmtEventTime, humanizeEvent } from "./event_label";

describe("심각도", () => {
  it("풀어 준 일은 정상이다. 도메인 낱말로 추측하지 않는다", () => {
    expect(eventTone("caption_qc.hold_released")).toBe("ok");
    expect(eventTone("clip.deident_report_resumed")).toBe("ok");
  });

  it("로그인 잠금은 사람이 확인해야 하는 주의 기록이다", () => {
    expect(eventTone("auth.login_locked")).toBe("warn");
  });

  it("분석 재요청 묶음 기록은 결과와 무관하게 남아 실패 색을 쓰지 않는다", () => {
    expect(eventTone("maiu.retry_failed")).toBe("default");
    expect(humanizeEvent("maiu.retry_failed")).toBe("분석 재요청");
    expect(humanizeEvent("maiu.summary_retried")).toBe("장면 요약 재요청");
  });

  it("장면 요약 기록은 준비 진행의 단계 이름과 같은 «장면 요약»으로 쓴다", () => {
    expect(humanizeEvent("maiu.stage.summarize_requested")).toBe("장면 요약 요청");
    expect(humanizeEvent("maiu.stage.summarize_failed")).toBe("장면 요약 실패");
  });

  it("표에 없는 종류는 일반이다", () => {
    expect(eventTone("work.unknown_thing")).toBe("default");
  });
});

describe("이름", () => {
  it("종류마다 이름이 하나이고 낱말을 겹치지 않는다", () => {
    expect(humanizeEvent("caption_qc.pass")).toBe("품질검수 통과");
    expect(humanizeEvent("caption_qc.failed")).toBe("품질검수 실행 실패");
    expect(humanizeEvent("auth.login_failed")).toBe("로그인 실패");
    expect(humanizeEvent("auth.login_locked")).toBe("로그인 잠금");
    expect(humanizeEvent("connection.health_changed")).toBe("연결 상태 변경");
  });

  it("동작 낱말이 같아도 도메인마다 이름을 따로 둔다", () => {
    expect(humanizeEvent("member.role_changed")).toBe("멤버 역할 변경");
    expect(humanizeEvent("user.role_changed")).toBe("사용자 역할 변경");
  });

  it("분석 서비스 이름을 쓰지 않는다", () => {
    for (const def of Object.values(EVENTS)) {
      expect(def.label).not.toMatch(/MI|MAIU|벤더/);
    }
    expect(humanizeEvent("maiu.brand_new")).toBe("분석 brand_new");
  });

  it("비식별화 신고는 용어집의 이름이다", () => {
    expect(humanizeEvent("clip.deident_miss_reported")).toBe("클립 비식별화 미흡 신고");
  });

  it("표에 없는 종류는 도메인 이름과 원문 동작으로 보인다", () => {
    expect(humanizeEvent("work.brand_new")).toBe("작업 brand_new");
    expect(humanizeEvent("nodomain")).toBe("nodomain");
  });
});

describe("시각", () => {
  it("두 장부가 같은 형식을 쓴다", () => {
    const iso = new Date(2026, 8, 23, 19, 12, 14, 5).toISOString();
    expect(fmtEventTime(iso)).toBe("2026-09-23 19:12:14");
    expect(fmtEventTime(iso, true)).toBe("2026-09-23 19:12:14.005");
  });
});

describe("분석 이벤트 이름", () => {
  it("사람의 분석 요청과 자동으로 넘어간 분석 시작은 이름이 다르다", () => {
    // 같은 이름이면 한 번 요청한 영상의 활동 기록에 "분석 요청"이 두 번 찍혀 두 번 요청한 것처럼 읽힌다.
    expect(humanizeEvent("maiu.requested")).toBe("분석 요청");
    expect(humanizeEvent("maiu.stage.analyze_triggered")).toBe("분석 시작");
  });

  it("분석 단계 이벤트끼리 이름이 겹치지 않는다. 같은 실패를 두 경로가 남기는 한 쌍만 예외다", () => {
    // 요약 실패는 장면 요약 폴링(`summarize_failed`)과 단계 오류 처리(`summary_failed`)가 각각 남긴다.
    const SAME_EVENT = new Set(["maiu.stage.summarize_failed", "maiu.stage.summary_failed"]);
    const seen = new Map<string, string>();
    for (const [type, def] of Object.entries(EVENTS)) {
      if (!type.startsWith("maiu.") || SAME_EVENT.has(type)) continue;
      expect(seen.get(def.label), `${type} ↔ ${seen.get(def.label)}`).toBeUndefined();
      seen.set(def.label, type);
    }
  });
});
