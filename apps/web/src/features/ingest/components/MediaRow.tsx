/**
 * 등록 화면의 영상 행 부품.
 *
 * 등록 콘솔의 행은 정렬해서 비교하는 데이터가 아니라 지금 처리할 작업이라서 `DataTable`을 쓰지 않는다.
 * `DataTable`에 맞추면 썸네일 열 설정과 행마다 같은 액션 폭 상수가 필요하다.
 *
 * grid-template 한 줄로 썸네일, 본문, 메타, 액션 트랙을 고정한다.
 * 액션은 슬롯이라 행마다 다른 버튼을 넣어도 오른쪽 정렬선이 맞는다.
 * 썸네일 열은 목록 단위로 켜고 끈다(`thumb`). 목록에 썸네일이 하나도 없으면 빈 재생 아이콘만 줄지어 서므로 끈다.
 */
import type { ReactNode } from "react";
import { Play } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/** 행 그리드. [썸네일] [본문(가변)] [메타(내용 폭)] [액션(내용 폭)]. 썸네일 열은 켤 때만 있다. */
const GRID: Record<"thumb" | "none", string> = {
  thumb: "grid grid-cols-[3.5rem_minmax(0,1fr)_auto_auto] items-center gap-x-3",
  none: "grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3",
};

/** 영상 행 하나를 그린다. 막힌 행은 경로 대신 사유를 보여 준다. */
export function MediaRow({
  thumbnailUrl, thumb = true, title, subtitle, note, noteTone, noteTitle, meta, action, accent, onClick, dense,
}: {
  thumbnailUrl?: string | null;
  /** 썸네일 열을 그리면 true(기본). 목록에 썸네일이 하나도 없으면 부르는 쪽이 끈다. */
  thumb?: boolean;
  title: string;
  /** 경로, 방송사 같은 보조 식별 정보. 한 줄로 말줄임한다. */
  subtitle?: ReactNode;
  /** 사유나 안내. 막힌 행에서는 위치보다 이유가 중요해서 경로 대신 이 값을 보여 준다. */
  note?: ReactNode;
  /** 사유의 성격. `bad`는 막힌 사유, `warn`은 자동으로 풀릴 대기(보류), `mute`는 중립 문구다. */
  noteTone?: "bad" | "warn" | "mute";
  /** 마우스를 올리면 보일 전체 문구. 없으면 `note`가 문자열일 때 그 문자열이다. */
  noteTitle?: string;
  /** 길이, 상태 같은 오른쪽 정보 슬롯. */
  meta?: ReactNode;
  action?: ReactNode;
  /** 왼쪽 상태 줄무늬의 색 클래스(`TONE_FILL`). */
  accent?: string;
  onClick?: () => void;
  dense?: boolean;
}) {
  const interactive = !!onClick;
  return (
    <div
      className={cn(
        "relative border-b border-separator/60 last:border-b-0",
        interactive && "cursor-pointer transition-colors hover:bg-fill/60",
      )}
      onClick={onClick}
      {...(interactive
        ? {
            role: "button",
            tabIndex: 0,
            onKeyDown: (e: React.KeyboardEvent) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            },
          }
        : {})}
    >
      {accent && (
        <span className={cn("absolute inset-y-0 left-0 w-[3px]", accent)} aria-hidden="true" />
      )}
      <div className={cn(GRID[thumb ? "thumb" : "none"], dense ? "px-4 py-1.5" : "px-4 py-2.5")}>
        {thumb && <Thumbnail url={thumbnailUrl} />}
        <div className="min-w-0">
          <p className="truncate text-callout font-medium text-label">{title}</p>
          {note ? (
            /* 실패 사유는 두 줄까지 보여 주고 원문을 `title`로 남긴다. 사유는 괄호 안에 서버 오류 원문이 든 문장이라
               (`규격 검사를 하지 못했습니다 (…)`) 한 줄로 자르면 원인을 담은 괄호가 잘린다.
               중립 문구는 짧아서 한 줄로 두므로 행 높이는 사유가 있는 행에서만 늘어난다. */
            <p
              /* `note`는 `ReactNode`라서 문자열일 때만 `title`로 쓸 수 있다. */
              title={noteTitle ?? (typeof note === "string" ? note : undefined)}
              className={cn(
                "text-caption",
                noteTone === "bad"
                  ? "line-clamp-2 text-bad-text"
                  : noteTone === "warn" ? "truncate text-warn-text" : "truncate text-label-3",
              )}
            >
              {note}
            </p>
          ) : (
            subtitle && <p className="truncate text-caption text-label-3">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-3 justify-self-end text-caption text-label-3">{meta}</div>
        <div
          className="flex items-center justify-end gap-1.5 justify-self-end"
          onClick={(e) => e.stopPropagation()}
        >
          {action}
        </div>
      </div>
    </div>
  );
}

/** 썸네일. 없으면 흐린 재생 아이콘을 대신 둔다. 상태는 그림 대신 글자로 알린다. */
function Thumbnail({ url }: { url?: string | null }) {
  return (
    <span className="relative block aspect-video w-14 overflow-hidden rounded-control bg-fill">
      {url ? (
        <img src={url} alt="" loading="lazy" className="size-full object-cover" />
      ) : (
        <span className="flex size-full items-center justify-center text-label-4/50">
          <Play size={12} aria-hidden="true" />
        </span>
      )}
    </span>
  );
}

/** 행 묶음 컨테이너. 카드 안의 행을 세로로 쌓는다. 행 사이 구분선은 각 행의 border가 그린다. */
export function MediaRows({ children }: { children: ReactNode }) {
  return <div className="flex flex-col">{children}</div>;
}
