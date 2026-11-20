/** 검수 초안 복원·저장(`review_draft`) 검사. */
import { describe, expect, it } from "vitest";

import { modifyReason, readReviewDraft, reviewDraftBody, withReviewDraft } from "./review_draft";

describe("readReviewDraft", () => {
  it("초안이 없으면 모든 값이 비어 있다", () => {
    for (const raw of [null, undefined, {}, "x"]) {
      expect(readReviewDraft(raw)).toEqual({ marks: [], codes: [], legacyTags: null, extra: "" });
    }
  });

  it("옛 초안의 전체 수정본 키는 버리고 나머지는 복원한다", () => {
    const d = readReviewDraft({
      marks: [["s0", { status: "ok" }]], codes: ["typo"], extra: "보충",
      modBrief: "전체 수정본", modReason: "r", modBase: "label-a",
    });
    expect(d).toEqual({ marks: [["s0", { status: "ok" }]], codes: ["typo"], legacyTags: null, extra: "보충" });
  });

  it("코드가 없는 옛 초안은 칩 이름을 따로 돌려준다", () => {
    expect(readReviewDraft({ tags: ["오타"] }).legacyTags).toEqual(["오타"]);
    expect(readReviewDraft({ codes: [], tags: ["오타"] }).legacyTags).toBeNull();
  });

  it("저장한 본문을 그대로 다시 읽는다", () => {
    const state = { marks: [["s1", { status: "edit", text: "고침" }]] as [string, { status: "edit"; text: string }][],
      codes: ["c1"], extra: "메모" };
    expect(readReviewDraft(reviewDraftBody(state))).toEqual({ ...state, legacyTags: null });
  });
});

describe("modifyReason", () => {
  it("문장 표시만으로 고친 수를 쓴다", () => {
    expect(modifyReason({ edit: 1, del: 2 })).toBe("문장 1건 수정, 2건 삭제");
    expect(modifyReason({ edit: 0, del: 0 })).toBeUndefined();
  });
});

describe("withReviewDraft", () => {
  const first = reviewDraftBody({ marks: [], codes: [], extra: "처음 연 초안" });
  const cached = { work_item_id: "w-a", status: "submitted", review_draft: first as Record<string, unknown> };
  const latest = { marks: [["s0", { status: "ok" }]] as [string, { status: "ok" }][],
    codes: [], extra: "떠나기 직전에 고친 초안" };

  it("떠날 때 실은 초안을 돌아온 화면이 복원한다", () => {
    const back = withReviewDraft(cached, reviewDraftBody(latest));
    expect(readReviewDraft(back?.review_draft)).toEqual({ ...latest, legacyTags: null });
  });

  it("초안 말고 다른 값은 그대로 두고 캐시 객체를 바꾸지 않는다", () => {
    const back = withReviewDraft(cached, reviewDraftBody(latest));
    expect(back).not.toBe(cached);
    expect(back?.status).toBe("submitted");
    expect(cached.review_draft).toBe(first);
  });

  it("캐시에 작업이 없으면 만들지 않는다", () => {
    expect(withReviewDraft(undefined, reviewDraftBody(latest))).toBeUndefined();
  });
});
