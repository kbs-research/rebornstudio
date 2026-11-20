/**
 * Job 상세 목록의 정렬 컨트롤.
 *
 * 목록형 행에는 열 머리가 없으므로 정렬을 별도 컨트롤로 둔다.
 * 모양은 감사 로그, Job 보드와 같은 캡슐 칩(`ui/FilterMenu`)이다.
 *
 * 정렬은 받은 행 기준이다. 서버 정렬은 커서 페이지와 얽혀 있어 따로 다룬다.
 * 받은 수가 전체보다 적으면 호출하는 쪽이 "N건 기준"이라고 밝혀, 일부만 정렬한 목록이 전체 정렬로 보이지 않게 한다.
 */
import { FilterMenu, type FilterOption } from "@/shared/ui/FilterMenu";

/** 정렬 기준 키. */
export type SortKey = "recent" | "oldest" | "longest" | "shortest" | "title";

/** 정렬 기준 라벨. */
export const SORT_LABEL: Record<SortKey, string> = {
  recent: "최신 등록순",
  oldest: "오래된 등록순",
  longest: "긴 영상순",
  shortest: "짧은 영상순",
  title: "제목순",
};

/** 행을 정렬한다. 화면마다 비교 함수를 다시 만들지 않도록 이 파일 한 곳에 둔다. */
export function sortRows<T extends {
  created_at: string; duration_seconds?: number | null; title: string;
}>(rows: T[], key: SortKey): T[] {
  const out = [...rows];
  switch (key) {
    case "oldest":
      return out.sort((a, b) => a.created_at.localeCompare(b.created_at));
    case "longest":
      return out.sort((a, b) => (b.duration_seconds ?? 0) - (a.duration_seconds ?? 0));
    case "shortest":
      return out.sort((a, b) => (a.duration_seconds ?? 0) - (b.duration_seconds ?? 0));
    case "title":
      return out.sort((a, b) => a.title.localeCompare(b.title, "ko"));
    default:
      return out.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
}

/** 정렬 선택지. 기본값(최신 등록순)은 FilterMenu의 '전체' 항목이고 빈 값이다. */
const SORT_OPTIONS: FilterOption[] = (Object.keys(SORT_LABEL) as SortKey[])
  .filter((k) => k !== "recent")
  .map((k) => ({ value: k, label: SORT_LABEL[k] }));

/** 정렬 메뉴를 그린다. 정렬 범위가 전체가 아니면 옆에 범위 안내를 붙인다. */
export function SortControl({
  value, onChange, scopeNote,
}: {
  value: SortKey;
  onChange: (k: SortKey) => void;
  /** "표시분 100건 기준" 같은 범위 안내. 전체를 정렬하지 않을 때는 반드시 넘긴다. */
  scopeNote?: string;
}) {
  return (
    <span className="flex items-center gap-2">
      <FilterMenu
        label="정렬"
        value={value === "recent" ? "" : value}
        options={SORT_OPTIONS}
        onChange={(v) => onChange((v || "recent") as SortKey)}
        align="right"
        width="w-40"
      />
      {scopeNote && <span className="text-caption text-label-3">{scopeNote}</span>}
    </span>
  );
}
