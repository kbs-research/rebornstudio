/** 워크벤치 편집기 초기 내용 선택(`pickInitialContent`) 검사. */
import { describe, expect, it } from "vitest";

import { type InitialContentInput, pickInitialContent, withSavedDraft } from "./initial_content";

type Payload = { caption: { brief: string } };

const SAVED: Payload = { caption: { brief: "저장된 캡션" } };
const AI = "AI 장면 요약";

/**
 * 워크벤치 훅이 조회가 하나 도착할 때마다 하는 처리를 재현한다.
 * AI 초안은 작업당 한 번만 채우고, 복원은 AI 초안 출처를 지우지 않는다.
 */
function replay(snapshots: InitialContentInput<Payload>[]) {
  let caption: string | null = null;
  let seedSource: string | null = null;
  let seeded = false;
  for (const s of snapshots) {
    const next = pickInitialContent(s);
    if (next.kind === "restore") {
      caption = next.src.caption.brief;
      seeded = true;
    } else if (next.kind === "seed" && !seeded) {
      caption = next.text;
      seedSource = next.text;
      seeded = true;
    }
  }
  return { caption, seedSource };
}

describe("pickInitialContent", () => {
  it("임시저장 초안이 가장 먼저다", () => {
    expect(pickInitialContent({ draft: SAVED, labels: [], aiSummary: AI })).toEqual({ kind: "restore", src: SAVED });
    expect(pickInitialContent({ draft: SAVED, aiSummary: AI })).toEqual({ kind: "restore", src: SAVED });
  });

  it("초안이 없으면 마지막 제출 라벨을 복원한다", () => {
    const older: Payload = { caption: { brief: "처음 제출" } };
    expect(pickInitialContent({ labels: [{ label_data: older }, { label_data: SAVED }], aiSummary: AI }))
      .toEqual({ kind: "restore", src: SAVED });
  });

  it("라벨 이력이 도착하기 전에는 AI 초안을 시드하지 않는다", () => {
    expect(pickInitialContent({ labels: undefined, aiSummary: AI })).toEqual({ kind: "none" });
  });

  it("저장된 라벨이 없다고 확정되면 AI 초안을 시드한다", () => {
    expect(pickInitialContent({ labels: [], aiSummary: `  ${AI}  ` })).toEqual({ kind: "seed", text: AI });
    expect(pickInitialContent({ labels: [{ label_data: null }], aiSummary: AI })).toEqual({ kind: "seed", text: AI });
  });

  it("초안 없이 라벨 이력 조회가 실패하면 AI 초안을 시드하지 않고 실패로 알린다", () => {
    expect(pickInitialContent({ labels: undefined, labelsFailed: true, aiSummary: AI })).toEqual({ kind: "failed" });
  });

  it("라벨 이력 조회가 실패해도 임시저장 초안은 복원한다", () => {
    expect(pickInitialContent({ draft: SAVED, labels: undefined, labelsFailed: true, aiSummary: AI }))
      .toEqual({ kind: "restore", src: SAVED });
  });

  it("다시 조회해 이력이 오면 실패가 아니다", () => {
    expect(pickInitialContent({ labels: [{ label_data: SAVED }], labelsFailed: false, aiSummary: AI }))
      .toEqual({ kind: "restore", src: SAVED });
    expect(pickInitialContent({ labels: [], labelsFailed: false, aiSummary: AI })).toEqual({ kind: "seed", text: AI });
  });

  it("AI 요약이 비어 있으면 아무것도 하지 않는다", () => {
    expect(pickInitialContent({ labels: [], aiSummary: "   " })).toEqual({ kind: "none" });
    expect(pickInitialContent({ labels: [], aiSummary: null })).toEqual({ kind: "none" });
  });
});

describe("조회 도착 순서", () => {
  const clipFirst = (labels: Payload[]) => [
    { labels: undefined, aiSummary: undefined },
    { labels: undefined, aiSummary: AI },
    { labels: labels.map((l) => ({ label_data: l })), aiSummary: AI },
  ];
  const labelsFirst = (labels: Payload[]) => [
    { labels: undefined, aiSummary: undefined },
    { labels: labels.map((l) => ({ label_data: l })), aiSummary: undefined },
    { labels: labels.map((l) => ({ label_data: l })), aiSummary: AI },
  ];

  it("저장된 라벨이 있으면 클립이 먼저 와도 AI 초안 출처가 남지 않는다", () => {
    expect(replay(clipFirst([SAVED]))).toEqual({ caption: "저장된 캡션", seedSource: null });
  });

  it("저장된 라벨이 있으면 이력이 먼저 와도 AI 초안 출처가 남지 않는다", () => {
    expect(replay(labelsFirst([SAVED]))).toEqual({ caption: "저장된 캡션", seedSource: null });
  });

  it("이력 조회가 실패했다가 다시 시도해 성공하면 저장된 라벨을 복원한다", () => {
    expect(replay([
      { labels: undefined, aiSummary: AI },
      { labels: undefined, labelsFailed: true, aiSummary: AI },
      { labels: [{ label_data: SAVED }], labelsFailed: false, aiSummary: AI },
    ])).toEqual({ caption: "저장된 캡션", seedSource: null });
  });

  it("저장된 라벨이 없으면 어느 순서든 AI 초안으로 채운다", () => {
    expect(replay(clipFirst([]))).toEqual({ caption: AI, seedSource: AI });
    expect(replay(labelsFirst([]))).toEqual({ caption: AI, seedSource: AI });
  });
});

describe("withSavedDraft", () => {
  const first: Payload = { caption: { brief: "처음 연 초안" } };
  const saved: Payload = { caption: { brief: "떠나기 전에 저장한 초안" } };
  const cached = { work_item_id: "w-a", status: "claimed", draft: first as Record<string, unknown> };

  it("떠날 때 실은 초안을 돌아온 화면이 복원한다", () => {
    const back = withSavedDraft(cached, saved);
    expect(pickInitialContent({ draft: back?.draft as Payload, labels: [], aiSummary: AI }))
      .toEqual({ kind: "restore", src: saved });
  });

  it("초안 말고 다른 값은 그대로 두고 캐시 객체를 바꾸지 않는다", () => {
    const back = withSavedDraft(cached, saved);
    expect(back).not.toBe(cached);
    expect(back?.status).toBe("claimed");
    expect(cached.draft).toBe(first);
  });

  it("캐시에 작업이 없으면 만들지 않는다", () => {
    expect(withSavedDraft(undefined, saved)).toBeUndefined();
  });
});
