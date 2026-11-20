/**
 * 등록 묶음 메타 일괄 입력 창이 쓰는 상한, 판정 이름, 반영 요청 만들기.
 *
 * 행을 영상과 맞추고 값을 검사하는 일은 서버(`media.meta_import`)가 한다.
 * 화면은 서버가 돌려준 판정에 이름을 붙이고, 판정이 `changed`인 행만 골라 반영 요청을 만든다.
 */
import { VIDEO_META_NAME, airedDate } from "@/lib/video_meta";

import type { MetaImportItem, MetaImportPreview, MetaImportRow } from "../api";

/** 올릴 수 있는 파일의 최대 크기(바이트). 서버 `meta_sheet.MAX_FILE_BYTES`와 같고 서버 테스트가 대조한다. */
export const META_IMPORT_MAX_BYTES = 1024 * 1024;

/** 한 파일에서 읽는 최대 데이터 행 수. 서버 `BULK_META_MAX_ROWS`와 같고 서버 테스트가 대조한다. */
export const META_IMPORT_MAX_ROWS = 5_000;

/** 올릴 수 있는 파일 형식. 파일 고르기 창의 `accept`에 쓴다. */
export const META_IMPORT_ACCEPT = ".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

type Field = NonNullable<MetaImportRow["changes"]>[number]["field"];
type Status = MetaImportRow["status"];

/** 필드의 화면 이름. 영상 상세와 같은 이름을 쓴다. */
export const META_FIELD_NAME: Record<Field, string> = {
  title: "제목",
  broadcaster: VIDEO_META_NAME.broadcaster,
  program_name: VIDEO_META_NAME.program_name,
  genre: VIDEO_META_NAME.genre,
  episode: "회차",
  aired_at: VIDEO_META_NAME.aired_at,
};

/** 행 판정의 이름과 칩 색. 요약 줄과 표의 판정 열이 같은 이름을 쓴다. */
export const META_ROW_STATUS: Record<Status, { label: string; tone: "ok" | "neutral" | "bad" | "warn" }> = {
  changed: { label: "변경", tone: "ok" },
  unchanged: { label: "변경 없음", tone: "neutral" },
  invalid: { label: "오류", tone: "bad" },
  unmatched: { label: "일치 없음", tone: "warn" },
  ambiguous: { label: "중복 일치", tone: "warn" },
};

/** 요약 줄의 판정 순서. 반영되는 것부터 둔다. */
export const META_STATUS_ORDER: Status[] = ["changed", "unchanged", "invalid", "unmatched", "ambiguous"];

/** 바뀌는 값 하나를 화면에 쓸 글자로 바꾼다. 방영일은 KST 날짜로, 빈 값은 `없음`으로 쓴다. */
export function metaValueText(field: Field, value: string | null | undefined): string {
  if (value == null || value === "") return "없음";
  if (field === "aired_at") return airedDate(value) ?? value;
  return value;
}

/** 판정이 `changed`인 행으로 반영 요청을 만든다. 오류·일치 없음·중복 일치·변경 없음 행은 넣지 않는다. */
export function metaApplyItems(preview: MetaImportPreview): MetaImportItem[] {
  return preview.rows
    .filter((r) => r.status === "changed" && r.video_id)
    .map((r) => ({
      video_id: r.video_id as string,
      fields: Object.fromEntries((r.changes ?? []).map((c) => [c.field, c.after])),
    }));
}

/** 반영하지 않는 행 수. 오류·일치 없음·중복 일치를 더한다. 변경 없음은 반영할 것이 없어 세지 않는다. */
export function metaSkippedRows(preview: MetaImportPreview): number {
  const c = preview.counts;
  return (c.invalid ?? 0) + (c.unmatched ?? 0) + (c.ambiguous ?? 0);
}
