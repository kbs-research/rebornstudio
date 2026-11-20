import { describe, expect, it } from "vitest";

import { BLOCKED_AT } from "./vocab";
import { isSummaryBlocked, retryFailTitle, retryToast, SUMMARY_CONFIRM, SUMMARY_RETRY_LABEL } from "./retry_text";

const base = { requeued: 0, restarted: 0, resummarized: 0, resummarize_failed: 0 };

describe("요약 재요청 문구", () => {
  it("확인 창은 요약만 다시 요청한다는 사실과 그대로 두는 것을 두 줄로 쓰고 금액을 싣지 않는다", () => {
    expect(SUMMARY_RETRY_LABEL).toBe("요약 재요청");
    expect(SUMMARY_CONFIRM.lead).toBe("장면 요약만 다시 요청합니다.");
    expect(SUMMARY_CONFIRM.sub).toBe("클립과 진행 중인 작업은 그대로 둡니다.");
    expect(`${SUMMARY_CONFIRM.lead}${SUMMARY_CONFIRM.sub}`).not.toMatch(/₩|원|비용/);
  });

  it("서버가 막힌 위치를 summary로 준 영상만 요약 재요청으로 연다", () => {
    expect(isSummaryBlocked({ prep_blocked_stage: "summary" })).toBe(true);
    expect(isSummaryBlocked({ prep_blocked_stage: "analysis" })).toBe(false);
    expect(isSummaryBlocked({ prep_blocked_stage: null })).toBe(false);
    expect(BLOCKED_AT.summary).toBe("장면 요약");
  });
});

describe("retryFailTitle", () => {
  it("요청이 실패하면 누른 버튼의 이름 뒤에 실패를 붙인다", () => {
    expect(retryFailTitle(true)).toBe("요약 재요청 실패");
    expect(retryFailTitle(false)).toBe("분석 재요청 실패");
  });
});

describe("retryToast", () => {
  it("요약 재요청이 받아들여지면 요약 재요청으로 알린다", () => {
    expect(retryToast({ ...base, resummarized: 1 }, 1)).toEqual({
      tone: "success", title: "요약 재요청 1개", detail: SUMMARY_CONFIRM.sub,
    });
  });

  it("요약 요청을 분석 서비스가 받지 않으면 오류로 알리고 사유 위치를 안내한다", () => {
    const t = retryToast({ ...base, resummarize_failed: 1 }, 1);
    expect(t.tone).toBe("error");
    expect(t.title).toBe("영상 1개 요약 재요청 실패");
  });

  it("분석 재요청은 지금처럼 등록된 파일을 그대로 쓴다고 알린다", () => {
    expect(retryToast({ ...base, requeued: 2 }, 2)).toEqual({
      tone: "success", title: "분석 재요청 2개", detail: "등록된 파일을 그대로 씁니다.",
    });
    expect(retryToast({ ...base, restarted: 1 }, 1).detail).toBe("1개는 분석 등록부터 다시 시작합니다.");
  });

  it("처리한 영상이 없으면 경고로 알린다. 이전 서버 응답(새 키 없음)도 받는다", () => {
    expect(retryToast({ requeued: 0, restarted: 0 }, 3)).toEqual({
      tone: "warning", title: "다시 요청할 실패 영상이 없습니다", detail: "3개 모두 재요청할 상태가 아닙니다.",
    });
  });
});
