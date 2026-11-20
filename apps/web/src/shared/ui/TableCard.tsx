/**
 * 섹션 카드(Card), SectionHeader, bare DataTable을 합친 컴포넌트.
 *
 * 표 하나짜리 페이지(사용자·감사 로그 등)를 받은 작업함 큐 테이블과 같은 카드 모양으로 통일한다.
 * 표 props는 DataTable에 그대로 넘기고, 머리글(아이콘·제목·보조 설명·카운트·액션)만 따로 받는다.
 */
import type { ReactNode } from "react";

import { Card } from "./Card";
import { DataTable, type DataTableProps } from "./DataTable";
import { SectionHeader } from "./SectionHeader";

type TableCardProps<T> = DataTableProps<T> & {
  icon?: ReactNode;
  title: ReactNode;
  /** 제목 옆의 회색 보조 설명. */
  description?: ReactNode;
  /** 오른쪽 카운트(건수 등). 주지 않으면 rows.length로 `전체 N건`을 만든다. */
  count?: ReactNode;
  /** 오른쪽 액션(버튼 등). */
  action?: ReactNode;
};

/** 카드 안에 머리글과 표를 그린다. */
export function TableCard<T>({
  icon,
  title,
  description,
  count,
  action,
  ...table
}: TableCardProps<T>) {
  return (
    <Card>
      <SectionHeader
        icon={icon}
        title={title}
        description={description}
        count={count ?? `전체 ${table.rows.length.toLocaleString("ko-KR")}건`}
        action={action}
      />
      <DataTable<T> bare {...table} />
    </Card>
  );
}
