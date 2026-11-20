/** 영상 준비 단계 문구 검사. 막힌 영상을 기다리는 중으로 표시하지 않는지 확인한다. */
import { describe, expect, it } from "vitest";

import { stageChip, stageEmpty } from "./video_stage";

describe("stageChip", () => {
  it("막힌 영상은 위치를 붙인 문제 칩이다", () => {
    expect(stageChip({ prep_stage: "blocked", prep_blocked_stage: "transcode" }))
      .toEqual({ label: "문제 · 분석본 변환", tone: "bad" });
  });

  it("단계를 모르면 칩을 그리지 않는다", () => {
    expect(stageChip({ prep_stage: null })).toBeNull();
    expect(stageChip({})).toBeNull();
  });

  it("분석 요청 대기는 중립 톤이다", () => {
    expect(stageChip({ prep_stage: "awaiting_analysis" })).toEqual({ label: "분석 요청 대기", tone: "neutral" });
  });
});

describe("stageEmpty", () => {
  it("변환이 실패한 영상은 기다리라고 하지 않고 구간 추가를 권하지 않는다", () => {
    const e = stageEmpty({ prep_stage: "blocked", prep_blocked_stage: "transcode" }, true);
    expect(e?.text).toBe("분석본 변환이 실패해 분석을 시작하지 못했습니다.");
    expect(e?.next).toBe("prep");
    expect(e?.add).toBe("hidden");
  });

  it("관리자가 아니면 할 일 대신 알릴 곳을 말한다", () => {
    const e = stageEmpty({ prep_stage: "blocked", prep_blocked_stage: "analysis" }, false);
    expect(e?.detail).toBe("프로젝트 관리자에게 알려 주세요.");
    expect(e?.next).toBeNull();
  });

  it("장면이 검출되지 않은 영상은 직접 추가가 주된 길이다", () => {
    expect(stageEmpty({ prep_stage: "blocked", prep_blocked_stage: "no_clips" }, true)?.add).toBe("primary");
  });

  it("분석 요청 대기는 분석을 요청하라고 말한다", () => {
    const e = stageEmpty({ prep_stage: "awaiting_analysis" }, true);
    expect(e?.text).toBe("분석 요청 전입니다.");
    expect(e?.next).toBe("ingest");
  });

  it("진행 중인 단계는 장면 분할 단계에 맡긴다", () => {
    expect(stageEmpty({ prep_stage: "analyzing" }, true)).toBeNull();
    expect(stageEmpty({ prep_stage: "ready_for_work" }, true)).toBeNull();
  });
});
