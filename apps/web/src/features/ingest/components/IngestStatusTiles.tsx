/**
 * 영상 등록 단계의 상태 타일. 요청 대기·분석 중·문제 세 상태의 영상 수를 한 줄에 둔다.
 *
 * 수는 서버 집계(`videos/buckets`)이고 목록 필터와 같은 SQL 규칙을 쓴다.
 * 타일은 수를 보여 주기만 한다. 분석 요청과 문제 조치는 등록 묶음 상세에서 한다.
 * 호박색 가장자리는 비용 결정이 필요한 요청 대기 한 곳에만 준다(`emphasize`).
 * 타일 아래 줄은 서버가 전체를 센 값이거나, 목록을 다 받았을 때만 채운다. 일부만 받은 목록으로 합계를 만들지 않는다.
 */
import { cn } from "@/shared/lib/cn";

/** 타일 하나의 값. `key`는 상태 이름, `note`는 수를 읽는 조건이다(없으면 비운다). */
export interface IngestTile {
  key: "ready" | "analyzing" | "blocked";
  label: string;
  count: number;
  dot: string;
  note?: string;
}

/** 상태 타일을 그린다. 뺄 상태는 호출부가 거른다. */
export function IngestStatusTiles({
  tiles, emphasize,
}: {
  tiles: IngestTile[];
  emphasize?: IngestTile["key"];
}) {
  return (
    <ul
      className={cn(
        "grid gap-3",
        tiles.length === 4 ? "sm:grid-cols-2 xl:grid-cols-4" : "sm:grid-cols-3",
      )}
    >
      {tiles.map((t) => (
        <li
          key={t.key}
          className={cn(
            "flex h-full w-full flex-col gap-1.5 rounded-card border border-separator bg-panel px-4 py-3.5 shadow-e1",
            emphasize === t.key && t.count > 0 && "border-l-2 border-l-amber",
          )}
        >
            <span className="flex w-full items-center gap-2 text-footnote font-semibold text-label">
              <span className={cn("size-2 shrink-0 rounded-capsule", t.dot)} aria-hidden="true" />
              {t.label}
            </span>
            <span className="flex items-baseline gap-1">
              <span className="text-title-1 tabular-nums text-label">{t.count.toLocaleString("ko-KR")}</span>
              <span className="text-caption text-label-3">개</span>
            </span>
            <span className="min-h-4.5 text-caption tabular-nums text-label-3">{t.note ?? ""}</span>
        </li>
      ))}
    </ul>
  );
}
