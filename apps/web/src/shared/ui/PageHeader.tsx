/**
 * 페이지 머리글. 제목, 제목 옆 칩, 사실 줄, 보조 설명, 오른쪽 actions 슬롯으로 이루어진다.
 *
 * 경로(breadcrumb)는 셸 상단의 TopBar가 한 곳에서 그리므로 이 컴포넌트는 렌더하지 않는다.
 * 제목은 탭 제목으로 올린다(`useDocumentTitle`). 상세 화면에서는 브레드크럼의 끝 마디도 이 제목을 쓴다.
 * 손으로 그린 머리는 탭 제목 연결과 여백 규칙을 따로 챙겨야 해서 화면마다 어긋난다.
 * 그래서 상세 화면이 제목 옆 상태 칩과 사실 줄을 그릴 수 있게 `badge`·`meta` 슬롯을 둔다.
 */
import type { ReactNode } from "react";

import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";

interface Props {
  title: string;
  subtitle?: string;
  /** 제목 옆 칩 위치. 이 화면 대상의 상태 하나(`StatusBadge`·`Chip`)만 두고 버튼은 actions에 둔다. */
  badge?: ReactNode;
  /**
   * 제목 아래 사실 줄. 대상을 알아보는 값(클립 수·방송사·등록일)을 가운뎃점으로 잇는다.
   * `subtitle`과 둘 중 하나만 쓴다. 둘 다 있으면 `meta`만 그린다.
   */
  meta?: ReactNode;
  actions?: ReactNode;
  /**
   * 머리 아래 구분선을 그리려면 true. 기본은 false다. 화면 머리는 선 없이 제목만 둔다.
   * 아래에 카드 없이 본문이 바로 이어져 경계가 필요한 화면만 켠다.
   */
  divider?: boolean;
}

/** 페이지 제목 머리글을 그린다. 제목은 `h1`이고, 탭 제목으로도 올린다. */
export function PageHeader({ title, subtitle, badge, meta, actions, divider = false }: Props) {
  useDocumentTitle(title);
  return (
    <header className={divider ? "mb-6 border-b border-separator pb-4" : "mb-4"}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        {/* 제목(강)을 위에, 보조 설명(약)을 아래에 둔다.
            baseline으로 나란히 두면 둘이 한 덩어리로 읽혀 위계가 드러나지 않는다. */}
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <h1 className="min-w-0 text-title-1 text-label">{title}</h1>
            {badge != null && <span className="flex shrink-0 items-center gap-1.5">{badge}</span>}
          </div>
          {/* 부제는 선택이다. 쓸 때는 이 화면에서만 알 수 있는 사실 하나(기준·범위·분모)를 쓴다. */}
          {meta != null ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-footnote tabular-nums text-label-2">
              {meta}
            </div>
          ) : (
            subtitle && <p className="mt-1 truncate text-footnote text-label-2">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2 pt-0.5">{actions}</div>}
      </div>
    </header>
  );
}
