/**
 * 설정 화면의 한 줄과 그 묶음. 왼쪽에 이름과 설명, 오른쪽에 조작 요소를 둔다.
 *
 * 이름·설명·조작 요소의 위치가 항목마다 다르면 한 화면 안에서도 읽는 순서가 바뀌므로,
 * 설정 화면의 모든 항목이 이 모양을 쓴다.
 * 긴 입력(여러 줄 글, 이름 같은 자유 텍스트)은 `stacked`로 이름 아래에 전체 폭으로 둔다.
 */
import type { ReactNode } from "react";

import { Card } from "@/shared/ui/Card";
import { cn } from "@/shared/lib/cn";

/** 설정 줄을 담는 카드를 그린다. 줄 사이에 구분선을 긋는다. */
export function SettingCard({
  children,
  className,
  "aria-label": ariaLabel,
}: {
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <Card aria-label={ariaLabel} className={cn("divide-y divide-separator", className)}>
      {children}
    </Card>
  );
}

interface RowProps {
  /** 항목 이름. */
  label: ReactNode;
  /** 이름이 가리킬 입력 요소의 id(`<label for>`). 스위치처럼 버튼이면 `labelId`를 쓴다. */
  htmlFor?: string;
  /** 이름 요소에 붙일 id. 스위치의 `labelledBy`가 이 값을 가리킨다. */
  labelId?: string;
  /** 이 항목이 무엇을 바꾸는지의 설명. 이름에 없는 말을 쓴다. */
  description?: ReactNode;
  /** 오른쪽 조작 요소(스위치, 숫자 입력, 선택 목록). */
  control?: ReactNode;
  /** 이름 옆에 붙일 작은 표시(예: 변경됨). */
  badge?: ReactNode;
  /** 조작 요소를 이름 아래 전체 폭으로 두려면 true. */
  stacked?: boolean;
  /** 줄 아래에 이어지는 내용(오류 문구, 켰을 때만 보이는 하위 설정). */
  children?: ReactNode;
}

/** 설정 한 줄을 그린다. */
export function SettingRow({
  label,
  htmlFor,
  labelId,
  description,
  control,
  badge,
  stacked = false,
  children,
}: RowProps) {
  const name = htmlFor ? (
    <label id={labelId} htmlFor={htmlFor} className="text-callout font-semibold text-label">
      {label}
    </label>
  ) : (
    <div id={labelId} className="text-callout font-semibold text-label">{label}</div>
  );
  return (
    <div className="px-5 py-4">
      <div className={cn("flex gap-6", stacked ? "flex-col gap-2" : "items-center")}>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2">
            {name}
            {badge}
          </div>
          {/* 설명은 읽어야 하는 본문이라 `text-label-2`로 쓴다. 보조 글자(`text-label-3`)보다 한 단계 진하게 두어 항목 이름 다음으로 읽힌다. */}
          {description && <div className="mt-1 text-footnote text-label-2">{description}</div>}
        </div>
        {control}
      </div>
      {children}
    </div>
  );
}
