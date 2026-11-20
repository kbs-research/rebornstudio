/**
 * 검수 초안의 모양. 검수 워크벤치(`pages/review.tsx`)가 초안 복원·저장·판정에 쓴다.
 * 이 판정만 따로 테스트하려고 컴포넌트 밖에 둔다.
 */
import type { SentenceMark } from "@/features/workbench";

/** 화면이 복원하는 검수 초안. 빠진 값은 기본값으로 채운다. */
export type ReviewDraft = {
  marks: [string, SentenceMark][];
  codes: string[];
  /** 반려 사유를 코드 대신 이름으로 저장한 이전 형식 초안의 칩. 코드가 있으면 `null`. */
  legacyTags: string[] | null;
  extra: string;
};

/**
 * 서버에 저장하는 초안의 키. 이전 형식의 초안과 같은 키를 쓴다.
 *
 * 이전 초안에 남은 전체 수정본 키(`modBrief`·`modReason`·`modBase`)는 읽지 않는다.
 */
type StoredDraft = {
  marks?: [string, SentenceMark][];
  codes?: string[];
  tags?: string[];
  extra?: string;
};

/** 서버 초안을 화면 상태로 읽는다. 빠진 값은 기본값으로 채우므로 앞 작업의 값이 남지 않는다. */
export function readReviewDraft(raw: unknown): ReviewDraft {
  const d = (raw && typeof raw === "object" ? raw : {}) as StoredDraft;
  const codes = Array.isArray(d.codes) ? d.codes : null;
  return {
    marks: Array.isArray(d.marks) ? d.marks : [],
    codes: codes ?? [],
    legacyTags: !codes && Array.isArray(d.tags) && d.tags.length > 0 ? d.tags : null,
    extra: typeof d.extra === "string" ? d.extra : "",
  };
}

/** 화면 상태를 서버에 저장할 초안 본문으로 만든다. `readReviewDraft`가 다시 읽는 모양이다. */
export function reviewDraftBody(d: Omit<ReviewDraft, "legacyTags">): StoredDraft {
  return { marks: d.marks, codes: d.codes, extra: d.extra };
}

/**
 * 수정 후 승인의 사유 문장을 고친 문장 수로 만든다.
 *
 * 고친 것이 없으면 `undefined`이다. 수가 빠진 문구("문장 ")를 사유로 보내지 않는다.
 */
export function modifyReason(counts: { edit: number; del: number }): string | undefined {
  const parts = [counts.edit && `${counts.edit}건 수정`, counts.del && `${counts.del}건 삭제`].filter(Boolean);
  return parts.length > 0 ? `문장 ${parts.join(", ")}` : undefined;
}

/**
 * 작업 캐시 값에 검수 초안을 싣는다. 캐시에 작업이 없으면 `undefined`를 그대로 돌려준다.
 *
 * 검수 화면은 건마다 새로 마운트하고, 새 화면은 캐시에 남은 작업으로 초안을 바로 복원한다.
 * 초안 저장은 캐시를 고치지 않는다. 떠날 때 마지막 초안을 싣지 않으면 돌아온 화면이 처음 받은 이전 초안을 복원한다.
 * 그러면 이어지는 자동 저장이 서버 초안을 그 이전 값으로 덮는다.
 */
export function withReviewDraft<T extends { review_draft?: unknown }>(
  item: T | undefined, body: StoredDraft,
): T | undefined {
  return item && { ...item, review_draft: body };
}
