/**
 * 정렬·검색·페이지를 갖춘 범용 표 컴포넌트.
 *
 * 앱의 모든 표가 이 컴포넌트를 쓴다. 구분선은 hairline, 수는 tabular-nums, hover는 옅게 둔다.
 * 정렬, 검색창 하나, 페이지네이션(pageSize), 로딩 Skeleton, EmptyState를 내장한다.
 * 열 필터와 열 표시·행 간격 설정은 두지 않는다. 표 안에서 행을 좁히는 도구는 검색창 하나다.
 * `bare`는 Card 안에 넣을 때 쓴다.
 */
import { Fragment, useEffect, useRef, useState } from "react";
import type { JSX, ReactNode } from "react";
import { Check, ChevronDown, ChevronUp, Search, X } from "lucide-react";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/Button";
import { Checkbox } from "@/shared/ui/Checkbox";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Skeleton } from "./Skeleton";
import { clampPage, tableCountLabel, type TableUrlBinding } from "./table_state";
import { DefinedTerm } from "./Tooltip";

export { tableCountLabel, useTableUrlState, type TableUrlBinding } from "./table_state";

// ─── 열 종류 ───────────────────────────────────────────────────────────────────

/**
 * 열 데이터의 종류. 수·날짜 열의 고정폭 숫자와 잠긴 식별 열(`title`)을 정한다.
 * 머리글에 종류 아이콘을 그리지 않는다. 아이콘은 열 이름에 이미 있는 정보를 되풀이하고,
 * 열마다 회색 아이콘이 붙으면 정렬 캐럿·정의 밑줄 같은 실제 신호가 묻힌다.
 */
export type ColumnKind =
  | "title"
  | "text"
  | "select"
  | "status"
  | "person"
  | "date"
  | "number"
  | "relation"
  | "image";

// ─── 공개 API 타입 ─────────────────────────────────────────────────────────────

/** 열 하나의 정의. 정렬·검색용 값 접근자와 셀 렌더러를 나눠 타입 안전하게 구성한다. */
export interface DataColumn<T> {
  /** 머리글 라벨. */
  label: string;
  /**
   * 이 열의 수가 무엇을 세는지, 어느 기간인지, 어느 쪽이 좋은지 알리는 정의.
   *
   * 주면 머리글에 점선 밑줄이 붙고 hover 때 정의가 나온다.
   * 점선은 정의가 있다는 뜻이고 실선(링크)과 구분된다.
   * 매일 보는 화면이라 설명은 필요할 때만 열어 보게 한다.
   *
   * 뜻이 분명한 열(`시각`·`제목`)에는 주지 않는다. 점선이 흔해지면 정말 필요한 열에서 눈에 띄지 않는다.
   */
  define?: ReactNode;
  /** 열 데이터의 종류. `number`·`date`는 고정폭 숫자로 그린다. `title`은 행을 가려내는 식별 열이다. */
  kind?: ColumnKind;
  /** 셀 정렬. 오른쪽 정렬이면 "right". */
  align?: "right";
  /**
   * 글자를 하나씩 대조하는 값이면 true(작업 ID, 해시, 코드).
   *
   * 수의 자릿수 정렬은 `kind: "number" | "date"`가 처리하므로 수에는 주지 않는다.
   * `font-mono`는 글꼴 자체를 바꾸어 그 열만 다른 서체가 되고, 한 표 안에 글꼴이 두 개가 된다.
   */
  mono?: boolean;
  /**
   * 셀 글자의 강조 단계. 글자 크기와 색을 열마다 손으로 정하지 않는다.
   *
   * 호출부가 셀 안에 크기·색 클래스를 직접 조합하면 같은 종류의 값이 표마다 다르게 그려진다.
   * 기본(주지 않음)은 이 표가 보여 주려는 값, `muted`는 함께 보여 주는 맥락(시각·경과),
   * `faint`는 없음·보조 표시다.
   */
  tone?: "muted" | "faint";
  /**
   * 정렬·검색용 값 접근자. 문자열은 한국어 localeCompare로 비교한다.
   *
   * 값이 없으면 대체값(`?? -1`·`?? 101`) 대신 `null`을 준다.
   * 정렬이 `null`을 방향과 관계없이 맨 뒤로 보낸다(아래 `DataTable`의 정렬 비교 참고).
   */
  value?: (row: T) => string | number | null | undefined;
  /** 셀 렌더러. 주지 않으면 value() 결과를 그대로 표시한다. */
  cell?: (row: T) => ReactNode;
  /**
   * 검색창이 대조할 글자. 주지 않으면 `value`를 쓰고, `false`면 이 열은 검색하지 않는다.
   *
   * 검색은 화면에 보이는 글자를 대조한다. `value`가 코드(`super_admin`·`active`)인 열을 그대로 검색하면
   * `_`나 `user`처럼 보이지 않는 값이 맞고, 한 셀에 둘을 쓰는 열(이름과 이메일)은 하나로만 찾힌다.
   */
  searchText?: ((row: T) => string | null | undefined) | false;
  /** 정렬할 수 있으면 true. value가 있으면 기본값은 true. */
  sortable?: boolean;
  /** th의 width 속성(예: "8rem", "120px"). */
  width?: string;
  /**
   * 긴 글을 말줄임표로 자르는 열이면 true. 공간이 모자라면 이 열이 먼저 줄어든다.
   *
   * 셀의 최소 폭을 0으로 두므로 잘리지 않는 글(제목·코드)이 함께 있는 열에는 주지 않는다.
   * 그 글이 옆 셀로 넘친다.
   */
  shrink?: boolean;
}

/**
 * 행 밀도. 표를 쓰는 화면이 정한다.
 *
 * 훑어보며 고르는 관리 목록은 compact, 읽는 목록은 comfortable을 쓴다.
 * 행 높이는 대략 36 / 44 / 56px이다.
 */
export type Density = "compact" | "comfortable" | "spacious";

const DENSITY: Record<Density, { cell: string; head: string }> = {
  compact: { cell: "px-3 py-1.5", head: "px-3 py-1.5" },
  comfortable: { cell: "px-4 py-2.75", head: "px-4 py-2.25" },
  spacious: { cell: "px-4 py-4", head: "px-4 py-3" },
};

/** DataTable 컴포넌트의 props. */
export interface DataTableProps<T> {
  columns: DataColumn<T>[];
  rows: T[];
  /** 각 행의 React key를 만드는 함수. */
  rowKey: (row: T) => string;
  /**
   * 행을 부르는 이름. 선택 체크박스의 이름(`○○ 선택`)에 쓴다.
   * 없으면 페이지 안의 순번(`3번째 행 선택`)을 써서 행마다 다른 이름으로 읽히게 한다.
   */
  rowLabel?: (row: T) => string;
  /** 처음 정렬 상태. */
  initialSort?: { i: number; dir: "asc" | "desc" };
  /**
   * 정렬 상태를 부르는 쪽이 가질 때의 값. 주면 내부 상태 대신 이 값을 쓴다.
   * 주소(`?sort=`)에 남겨 새로고침·공유가 같은 순서를 열게 할 때 쓴다. `null`은 정렬 없음이다.
   */
  sortState?: { i: number; dir: "asc" | "desc" } | null;
  /** 머리글을 눌러 정렬이 바뀌면 부른다. `sortState`와 함께 준다. */
  onSortChange?: (next: { i: number; dir: "asc" | "desc" }) => void;
  /** 검색어를 부르는 쪽이 가질 때의 값. 주면 내부 상태 대신 이 값을 쓴다. */
  query?: string;
  /** 검색어가 바뀌면 부른다. `query`와 함께 준다. */
  onQueryChange?: (next: string) => void;
  /** 행 클릭 핸들러. 행 안의 버튼·링크·입력 요소를 누른 경우에는 부르지 않는다. */
  onRowClick?: (row: T) => void;
  /** 빈 상태를 대신할 노드. 주지 않거나 검색어가 있으면 DTEmpty를 쓴다. */
  empty?: ReactNode;
  /** 불러오는 중이면 true. Skeleton 4행을 표시한다. */
  loading?: boolean;
  /**
   * 조회 실패. 참이면 행이나 빈 상태 대신 `ErrorState`를 그린다.
   *
   * 비어 있는 상태와 불러오지 못한 상태를 나눈다. 나누지 않으면 실패가 `항목이 없습니다`로
   * 표시되고, 사용자는 할 일이 없다고 읽고 화면을 닫는다.
   * 전역 토스트는 5초 뒤 사라지지만 본문의 잘못된 표시는 남으므로 토스트는 보조 수단이다.
   *
   * `true` 대신 실패 객체(`q.error`)를 주면 403·404·409 같은 영구 실패에는 재시도 대신
   * 사유를 보여 준다(`ErrorState`의 `error`).
   */
  error?: unknown;
  /** 실패했을 때의 재시도 함수. 일시 오류(429·5xx·타임아웃)에서 다시 시도할 수 있게 한다. */
  onRetry?: () => void;
  /** 검색창. string이면 placeholder 텍스트로 쓰고, true면 기본 placeholder "검색…"을 쓴다. */
  search?: string | boolean;
  /** Card 안에 넣으면 true. 테두리가 겹치지 않게 바깥 테두리·라운드·그림자를 뺀다. */
  bare?: boolean;
  /**
   * 페이지당 행 수. 기본값은 25이고, 0이면 내장 페이지네이션을 끈다(서버 커서 표).
   * 켜져 있으면 행이 하나 이상일 때 표 아래에 이 페이지 건수와 페이지 컨트롤을 항상 보여 준다.
   */
  pageSize?: number;
  /** 행마다 더할 className. 완료·기각 행의 옅은 색처럼 행 단위 강조에 쓴다(hover와 함께 적용된다). */
  rowClassName?: (row: T) => string;
  /** 행 밀도. 주지 않으면 comfortable이다. */
  density?: Density;
  /**
   * 세로 열 구분선을 그리면 true. 기본값은 false이고 행 사이만 hairline으로 나뉜다.
   *
   * 세로선은 격자를 만들어 비어 있는 가운데는 더 비어 보이고 오른쪽 끝은 답답해 보인다.
   * 그래서 같은 행의 수를 열끼리 맞대어 읽는 비교표에만 true를 주고, 열어 볼 항목을 고르는 목록에는 주지 않는다.
   */
  columnRules?: boolean;
  /**
   * 행 앞쪽 상태 줄무늬의 색 클래스(예: `"bg-warn-text"`). 주지 않으면 줄무늬 열을 그리지 않는다.
   * 상태를 제목(왼쪽) 옆에 두어 시선이 제목과 상태(오른쪽) 사이를 오가지 않게 한다.
   */
  rowAccent?: (row: T) => string | undefined;
  /**
   * 서버 커서 페이지 표의 하단 영역. `pageSize={0}`으로 내장 페이지네이션을 끈 표가
   * "N건 표시 · 전체 M건 + 더 보기"를 여기에 넣는다.
   */
  footer?: ReactNode;
  /**
   * 행을 고를 수 있으면 true를 돌려주는 함수. 주면 체크박스 열이 생긴다.
   * 행마다 다른 이유(이슈 중 복구 가능한 것만 등)로 달라지므로 boolean 대신 함수로 받는다.
   */
  selectable?: (row: T) => boolean;
  /**
   * 선택 열이 없어도 그 폭만큼 빈 열을 두면 true. 선택 열이 있는 표와 위아래로 나란히 놓일 때만 쓴다.
   *
   * 등록 콘솔처럼 표 여러 개를 위아래로 쌓으면, 선택 열이 있는 표만 썸네일·제목이
   * 32px 밀려 왼쪽 선이 계단처럼 어긋난다.
   * 위치만 맞추는 용도라 머리글과 셀을 비워 둔다.
   */
  reserveSelection?: boolean;
  /** 선택된 rowKey 집합(제어 컴포넌트). */
  selected?: Set<string>;
  onSelectedChange?: (next: Set<string>) => void;
  /** 선택된 행이 있을 때 기본 툴바를 대신하는 툴바(선택 수·액션). */
  selectionToolbar?: ReactNode;
  /**
   * 호출부가 미리 거른 목록의 전체 수. 주면 분모 줄이 이 수를 분모로 쓴다(`13개 중 6개`).
   *
   * 검색·상태 필터를 표 밖에서 걸고 거른 행만 `rows`로 넘기는 화면이 쓴다. 주지 않으면
   * 거른 행이 전체로 읽혀 머리 칩(`13개 중 6개`)과 분모 줄(`전체 6개`)이 서로 다른 수를 표시한다.
   */
  total?: number;
  /**
   * 정렬·검색·페이지·행 수를 주소에 두는 연결(`useTableUrlState`). 주지 않으면 화면 상태에만 둔다.
   *
   * 주소에 두면 새로고침하거나 링크를 받은 사람이 같은 목록을 본다. 한 화면에 표가 둘 이상이면 접두어로 나눈다.
   */
  urlState?: TableUrlBinding;
  /** 분모 줄의 단위(`명`·`개`·`건`). 기본은 `건`. */
  unit?: string;
  /**
   * 좁은 폭(640px 미만)에서 행을 카드로 쌓으면 true. 셀마다 왼쪽에 열 이름, 오른쪽에 값을 둔다.
   *
   * 열이 셋 이상이고 값이 긴 글인 표에 준다. 375px 폭에 네 열을 나란히 두면 한 열이 60px 남짓이라
   * 글자가 한 자씩 끊기고, 가로 스크롤은 공개 화면에서 표가 잘린 것처럼 보인다.
   * 카드에는 행 선택·줄무늬 열을 둘 곳이 없으므로 두 열과 함께 쓰지 않는다.
   */
  stack?: boolean;
  /**
   * 행 앞에 넣을 전체 폭 묶음 머리 줄. 앞 행(`prev`, 이 페이지의 첫 행이면 `undefined`)과 비교해
   * 새 묶음이 시작되는 행에서만 내용을 돌려주고, 아니면 `null`을 돌려준다.
   *
   * 날짜별로 묶인 기록처럼 같은 값을 행마다 되풀이하지 않을 때 쓴다. 머리 줄은 데이터 행과 따로 그려서
   * 선택·키보드 이동·분모 줄에서 빠진다. 묶음은 보이는 순서대로 판정하므로 정렬하는 표에는 주지 않는다.
   */
  rowGroupHeader?: (row: T, prev: T | undefined) => ReactNode | null;
}

/** 쌓기 모드의 표·행·셀 클래스. 640px 미만에서만 적용되고 그 이상에서는 보통 표다. */
const STACK = {
  table: "max-sm:block",
  head: "max-sm:hidden",
  body: "max-sm:block",
  row: "max-sm:block max-sm:px-4 max-sm:py-3",
  cell:
    "max-sm:grid max-sm:grid-cols-[6.5rem_minmax(0,1fr)] max-sm:items-baseline max-sm:justify-items-start max-sm:gap-x-3 max-sm:px-0 max-sm:py-1 " +
    "max-sm:whitespace-normal max-sm:text-left max-sm:border-r-0 " +
    "max-sm:before:text-caption max-sm:before:font-semibold max-sm:before:text-label-2 " +
    "max-sm:before:content-[attr(data-label)]",
};

// ─── DTEmpty ──────────────────────────────────────────────────────────────────

interface DTEmptyProps {
  /** 검색어가 있으면 true. */
  hasQuery: boolean;
  /** "검색어 지우기" 버튼 클릭 핸들러. */
  onClear: () => void;
}

/**
 * 표의 빈 상태를 그린다. 검색어가 있는지에 따라 다른 문구와 지우기 버튼을 보여 준다.
 *
 * 호출부가 `empty=`로 넘기는 `EmptyState`와 같은 컴포넌트를 써서 한 화면에서 모양이 하나가 되게 한다.
 * 검색어가 있는지와 관계없이 그림도 같다.
 * 표 셀은 페이지 한가운데가 아니므로 `dense`를 쓴다.
 *
 * 머리글은 명사구로 쓰고 설명 문장은 두지 않는다. 설명은 머리글과 같은 말을 되풀이하고,
 * 다음 행동은 `검색어 지우기` 버튼에 이미 있다. 검색어 없이 빈 경우는 머리글만 둔다.
 */
function DTEmpty({ hasQuery, onClear }: DTEmptyProps) {
  return (
    <EmptyState
      dense
      message={hasQuery ? "검색 결과 없음" : "항목 없음"}
      action={
        hasQuery ? (
          <Button variant="ghost" size="sm" onClick={onClear}>
            검색어 지우기
          </Button>
        ) : undefined
      }
    />
  );
}

// ─── 행 수 칩(팝오버) ───────────────────────────────────────────────────────────

/** 페이지당 행 수 칩을 그린다. 누르면 팝오버에서 행 수를 고르고, 팝오버는 오른쪽에 맞춘다. */
function RowsPerPageChip({
  value,
  options,
  onChange,
}: {
  value: number;
  options: number[];
  onChange: (n: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClose(open, ref, () => setOpen(false));
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="inline-flex h-8 items-center gap-1 rounded-capsule border border-separator bg-canvas-2 pl-3 pr-2.5 text-footnote font-medium text-label-2 transition-colors hover:bg-fill"
      >
        <span className="tabular-nums">{value}행씩</span>
        <ChevronDown
          size={14}
          className={cn(
            "text-label-3 transition-transform",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute right-0 top-full z-30 mt-1.5 w-32 overflow-auto rounded-card border border-separator bg-panel py-1 shadow-pop"
        >
          {options.map((n) => (
            <ChipOption
              key={n}
              label={`${n}행씩`}
              selected={n === value}
              onClick={() => {
                onChange(n);
                setOpen(false);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** 팝오버의 옵션 한 줄을 그린다. 고른 옵션에는 체크 표시를 붙인다. */
function ChipOption({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-footnote transition-colors hover:bg-fill",
        selected ? "font-medium text-accent" : "text-label-2",
      )}
    >
      <span className="truncate">{label}</span>
      {selected && (
        <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />
      )}
    </button>
  );
}

// ─── DataTable ────────────────────────────────────────────────────────────────

const SKELETON_ROWS = 4;

/**
 * 정렬·검색·페이지를 갖춘 표를 그린다.
 *
 * 머리글을 누르면 오름차순과 내림차순을 바꾼다(캐럿 표시, 문자열은 한국어 localeCompare, 수는 뺄셈으로 비교).
 * 검색창은 모든 column.value에 검색어가 들어 있는지 대소문자 구분 없이 확인한다.
 * loading이면 Skeleton 4행을, error면 `ErrorState`를, 결과가 없으면 DTEmpty(또는 `empty`)를 그린다.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  rowLabel,
  initialSort,
  sortState,
  onSortChange,
  query,
  onQueryChange,
  onRowClick,
  empty,
  loading = false,
  error = false,
  onRetry,
  search,
  bare = false,
  pageSize = 25,
  rowClassName,
  density = "comfortable",
  columnRules = false,
  rowAccent,
  footer,
  selectable,
  reserveSelection,
  selected,
  onSelectedChange,
  selectionToolbar,
  unit = "건",
  stack = false,
  rowGroupHeader,
  total,
  urlState,
}: DataTableProps<T>): JSX.Element {
  /*
    주소에서 연 상태가 있으면 주소의 상태로 시작한다. 열은 인덱스 대신 이름으로 주소에 둔다.
    인덱스로 두면 열이 하나 늘어나는 순간 받은 링크가 다른 열을 가리킨다.
    부르는 쪽이 정렬(`sortState`)이나 검색어(`query`)를 가지면 부르는 쪽의 값을 우선한다.
  */
  const fromUrl = urlState?.initial;
  const labelIndex = (label: string | undefined) => {
    if (!label) return -1;
    return columns.findIndex((c) => c.label === label);
  };
  const [ownSort, setOwnSort] = useState<{ i: number; dir: "asc" | "desc" } | null>(() => {
    const i = labelIndex(fromUrl?.sort?.label);
    return i >= 0 && fromUrl?.sort ? { i, dir: fromUrl.sort.dir } : (initialSort ?? null);
  });
  const sort = sortState !== undefined ? sortState : ownSort;
  const [ownQ, setOwnQ] = useState(() => (search ? (fromUrl?.q ?? "") : ""));
  const q = query ?? ownQ;
  /** 검색어를 바꾼다. 부르는 쪽이 검색어를 가지면 `onQueryChange`로 알린다. */
  const setQ = (next: string) => (onQueryChange ? onQueryChange(next) : setOwnQ(next));
  const [page, setPage] = useState(() => fromUrl?.page ?? 1);
  // 표시 행 수. 사용자가 표마다 바꾼다(기본값은 pageSize prop). 0이면 페이지네이션을 끈다(커서 표 등).
  const [rowsPerPage, setRowsPerPage] = useState(() =>
    pageSize > 0 && fromUrl?.rows ? fromUrl.rows : pageSize,
  );
  const pad = DENSITY[density];
  const shownCount = columns.length;

  // 상단 툴바에는 검색창 하나만 둔다.
  const hasSearchBar = !!search;

  // ── 데이터 파이프라인: search → sort ─────────────────────────────────────
  let view = rows;

  // 검색어를 적용한다. 열마다 `searchText`(없으면 `value`)를 대조하고, 값이 없는 셀은 대조에서 뺀다.
  if (q && search) {
    const ql = q.trim().toLowerCase();
    if (ql) {
      view = view.filter((r) =>
        columns.some((c) => {
          if (c.searchText === false) return false;
          const v = c.searchText ? c.searchText(r) : c.value?.(r);
          return v != null && String(v).toLowerCase().includes(ql);
        }),
      );
    }
  }

  // 정렬을 적용한다.
  if (
    sort &&
    columns[sort.i] &&
    columns[sort.i].sortable !== false &&
    columns[sort.i].value
  ) {
    const acc = columns[sort.i].value!;
    view = [...view].sort((a, b) => {
      const va = acc(a);
      const vb = acc(b);
      /*
        값이 없는 행은 정렬 방향과 관계없이 맨 뒤에 두려고 방향을 뒤집기 전에 먼저 나눈다.
        그러면 열은 `?? null`만 주면 되고, 어느 방향이든 값이 있는 행이 앞에 온다.
        열마다 대체값(`?? 101`, `?? -1`)을 고르면 하나의 값이 두 방향 모두에서 뒤에 있을 수 없다.
        그래서 반대 방향에서는 값이 없는 행이 맨 앞으로 올라와 반려가 가장 적은 사람처럼 읽힌다.
      */
      const aNull = va == null || va === "";
      const bNull = vb == null || vb === "";
      if (aNull || bNull) return aNull && bNull ? 0 : aNull ? 1 : -1;
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb), "ko");
      return sort.dir === "desc" ? -cmp : cmp;
    });
  }

  // ── 페이지네이션(선택). 검색·정렬이 바뀌면 1페이지로 돌아가고 범위는 clamp한다 ──
  /*
    값이 실제로 바뀌었을 때만 돌린다. 그래야 주소에서 연 페이지(`?page=2`)를 첫 효과가 1로 지우지 않는다.
    개발 모드의 StrictMode는 효과를 두 번 부르고, 부르는 쪽이 정렬 객체를 렌더마다 새로 만들면
    같은 값으로도 효과가 다시 불린다. 그래서 첫 실행을 건너뛰는 대신 값으로 비교한다.
  */
  const resetKey = JSON.stringify([q, sort, rowsPerPage]);
  const lastResetKey = useRef(resetKey);
  useEffect(() => {
    if (lastResetKey.current === resetKey) return;
    lastResetKey.current = resetKey;
    setPage(1);
  }, [resetKey]);

  // 상태가 바뀌면 주소에 적는다. 기본값과 같은 항목은 적지 않아 주소가 짧게 남는다.
  const onUrlChange = urlState?.onChange;
  const sortLabel = sort ? columns[sort.i]?.label : undefined;
  const sortDir = sort?.dir;
  useEffect(() => {
    if (!onUrlChange) return;
    const initLabel = initialSort ? columns[initialSort.i]?.label : undefined;
    const isInitialSort = sortLabel === initLabel && (!initialSort || sortDir === initialSort.dir);
    onUrlChange({
      sort: sortLabel && sortDir && !isInitialSort ? { label: sortLabel, dir: sortDir } : null,
      q,
      page,
      rows: rowsPerPage !== pageSize ? rowsPerPage : null,
    });
    // 열 정의는 렌더마다 새로 만들어지므로 의존성에서 뺀다. 바뀌는 것은 아래 값들이다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onUrlChange, sortLabel, sortDir, q, page, rowsPerPage]);
  const paged = rowsPerPage > 0;
  const pageCount = paged
    ? Math.max(1, Math.ceil(view.length / rowsPerPage))
    : 1;
  const curPage = Math.min(page, pageCount);
  /* 범위 밖 페이지는 상태도 마지막 페이지로 맞춘다. 위 효과가 그 값을 주소에 다시 적어(replace) 보이는 페이지와 주소가 같아진다.
     받는 중에는 행 수 정보가 없어서 맞추지 않는다. */
  const settledPage = loading ? page : clampPage(page, view.length, rowsPerPage);
  useEffect(() => {
    if (settledPage !== page) setPage(settledPage);
  }, [settledPage, page]);
  const pageRows = paged
    ? view.slice((curPage - 1) * rowsPerPage, curPage * rowsPerPage)
    : view;
  /*
    행을 누를 수 있는 표는 탭 정지를 하나만 둔다(로빙 tabindex).
    행마다 `tabIndex={0}`을 주면 백 행에 탭 정지가 백 개라서, 표 뒤의 `더 보기`에 닿으려면 그만큼 눌러야 한다.
    표는 격자(ARIA grid)로 다룬다. Tab 키로는 표에 들어오고 나가며, 안에서는 방향키로 움직인다.
    `onRowClick`이 없는 표의 행은 탭 정지에 들지 않으므로 이 처리를 하지 않는다.
  */
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const [activeRow, setActiveRow] = useState(0);
  // 페이지·정렬·검색이 바뀌면 활성 행을 첫 행으로 되돌린다. 열 번째 행을 활성으로 둔 채
  // 페이지를 넘기면 새 페이지에 없는 행을 가리킬 수 있다.
  useEffect(() => {
    setActiveRow(0);
  }, [curPage, q, sort, rowsPerPage]);

  /*
    행 안의 버튼·링크도 활성 행에서만 탭 순서에 들어가게 한다. 그러지 않으면 행 안 요소만큼 탭 정지가 남는다.
    셀 내용은 쓰는 쪽이 넘긴 노드(`cell: (r) => …`)라서 JSX로는 바꿀 수 없으므로 렌더 뒤에 DOM에서 고친다.
    쓰는 쪽이 `tabindex`를 직접 준 요소는 이유가 있어 정한 값이므로 건드리지 않는다.
  */
  useEffect(() => {
    if (!onRowClick) return;
    const trs =
      bodyRef.current?.querySelectorAll<HTMLTableRowElement>(
        "tr[data-row-idx]",
      );
    trs?.forEach((tr, i) => {
      const on = i === activeRow;
      tr.querySelectorAll<HTMLElement>(
        "button, a[href], input, select, textarea",
      ).forEach((el) => {
        /*
          쓰는 쪽이 직접 준 `tabindex`는 건드리지 않는다.
          이 코드가 쓴 값도 속성으로 남으므로 표시(`data-roving`)로 구분한다.
          `hasAttribute`만 보면 두 번째 렌더부터 모두 건너뛰어 활성 행의 버튼이 -1에 고정된다.
        */
        if (el.hasAttribute("tabindex") && el.dataset.roving !== "1") return;
        el.dataset.roving = "1";
        el.tabIndex = on ? 0 : -1;
      });
    });
  });

  /** 방향키로 활성 행을 옮긴다. `Home`/`End`는 처음·끝으로 가고, 이동은 표 안에서만 한다. */
  const moveRow = (next: number) => {
    const n = pageRows.length;
    if (n === 0) return;
    const i = Math.min(n - 1, Math.max(0, next));
    setActiveRow(i);
    bodyRef.current
      ?.querySelector<HTMLTableRowElement>(`tr[data-row-idx="${i}"]`)
      ?.focus();
  };

  // 행 수 선택지. 공통 값과 표 기본값을 합치고 중복을 없애 오름차순으로 둔다.
  const pageSizeOptions = Array.from(
    new Set([10, 25, 50, 100, pageSize].filter((n) => n > 0)),
  ).sort((a, b) => a - b);

  /**
   * 행 수 칩. 페이지 컨트롤 옆에 둔다.
   *
   * 표 위에 따로 한 줄을 두면 검색창이 없는 표에서는 그 줄에 이 칩만 남아 폭 전체가
   * 빈 띠가 되고, 위의 `SectionHeader`까지 합쳐 표 하나에 머리글이 세 개가 된다(머리글, 빈 툴바, 열 머리글).
   * 행 수는 페이지 이동과 같은 일을 하므로 아래에 나란히 두면 찾기도 쉽다.
   */
  const viewTools =
    paged && view.length > pageSizeOptions[0] ? (
      <div className="flex shrink-0 items-center gap-1.5">
        <RowsPerPageChip
          value={rowsPerPage}
          options={pageSizeOptions}
          onChange={setRowsPerPage}
        />
      </div>
    ) : null;

  /** 머리글을 누르면 정렬을 바꾼다. 같은 열이면 방향을 뒤집고, 다른 열이면 오름차순으로 시작한다. */
  const toggle = (i: number) => {
    const next: { i: number; dir: "asc" | "desc" } =
      sort && sort.i === i ? { i, dir: sort.dir === "asc" ? "desc" : "asc" } : { i, dir: "asc" };
    if (onSortChange) onSortChange(next);
    else setOwnSort(next);
  };

  const hasQuery = !!q;

  /*
    보여 줄 행이 하나도 없으면 열 머리글을 그리지 않는다. 머리글 아래에 빈 상태가 나오면 빈 상태가 표의 부록처럼 보인다.
    검색 때문에 0행이면 머리글을 남긴다. 아무것도 없는 상태와 검색어에 맞는 행이 없는 상태를 구분한다.
    불러오는 중에도 남긴다. 그때 그리는 스켈레톤이 곧 올 표의 모양을 미리 보여 주고, 머리글이 그 모양의 절반이다.
  */
  // 실패했을 때도 머리글을 남긴다. 열 이름이 있어야 무엇을 불러오지 못했는지 알 수 있다.
  const showHead = view.length > 0 || hasQuery || loading || error;

  const handleClearAll = () => setQ("");

  const searchPlaceholder = typeof search === "string" ? search : "검색…";

  // ── 선택 ────────────────────────────────────────────────────────────────
  // 고를 수 있는 행에만 체크박스를 준다.
  const pickable = selectable ? pageRows.filter(selectable) : [];
  const chosenOnPage = pickable.filter((r) => selected?.has(rowKey(r)));
  const allPicked =
    pickable.length > 0 && chosenOnPage.length === pickable.length;
  const toggleRow = (r: T) => {
    if (!onSelectedChange) return;
    const next = new Set(selected);
    const k = rowKey(r);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    onSelectedChange(next);
  };
  const toggleAll = () => {
    if (!onSelectedChange) return;
    const next = new Set(selected);
    // '전체'의 범위는 이 페이지다. 화면 밖의 행은 고르지 않고, 선택 툴바가 범위를 밝힌다.
    if (allPicked) pickable.forEach((r) => next.delete(rowKey(r)));
    else pickable.forEach((r) => next.add(rowKey(r)));
    onSelectedChange(next);
  };
  const selCount = selected?.size ?? 0;
  const showSelectionBar = !!selectionToolbar && selCount > 0;
  /** 줄무늬·체크박스처럼 데이터가 아닌 앞쪽 열의 수. 빈 상태의 colSpan 계산에 쓴다. */
  const leadCols =
    (rowAccent ? 1 : 0) + (selectable || reserveSelection ? 1 : 0);

  return (
    <div>
      {/* 선택 중이면 선택 툴바가 기본 툴바를 대신한다. 도구가 두 줄로 쌓이면 지금 어느 도구가 유효한지 알기 어렵다. */}
      {showSelectionBar && (
        <div
          className={cn(
            "mb-2.5 flex flex-wrap items-center gap-2",
            bare && "px-4 pt-3",
          )}
        >
          {selectionToolbar}
        </div>
      )}

      {/* 툴바: 캡슐 검색창. 검색창이 있고 거를 행이 하나 이상일 때만 그린다.
          불러오는 중에는 남긴다. 툴바가 사라졌다 나타나면 표가 위아래로 흔들린다.
          행 수 칩은 아래 푸터의 페이지 컨트롤 옆에 둔다(`viewTools` 주석 참고).
          bare면 Card 안이므로 좌우·위 패딩으로 정렬한다. */}
      {!showSelectionBar && hasSearchBar && (rows.length > 0 || hasQuery || loading) && (
        <div
          className={cn(
            "mb-2.5 flex flex-wrap items-center gap-1.5",
            bare && "px-4 pt-3",
          )}
        >
          {/* 캡슐 검색창. 포커스되면 테두리가 액센트 색이 된다. */}
          {search && (
            <div className="flex h-8 w-64 max-w-full items-center gap-1.5 rounded-capsule border border-separator bg-canvas-2 px-3 transition-colors focus-within:border-focus focus-within:bg-canvas">
              <Search size={14} className="shrink-0 text-label-3" />
              <input
                type="text"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={searchPlaceholder}
                /*
                  placeholder는 글자를 입력하기 시작하면 사라지고, 보조 기술이 이름으로 읽어 줄지도 정해져 있지 않다.
                  그래서 접근성 이름을 따로 준다. 검색 입력란은 이 컴포넌트 하나라서 이름도 이 위치에서 한 번 준다.
                */
                aria-label={searchPlaceholder}
                className="w-full border-0 bg-transparent text-callout text-label outline-none placeholder:text-placeholder"
              />
              {q && (
                <button
                  type="button"
                  onClick={() => setQ("")}
                  className="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-label-3 hover:text-label-2"
                  aria-label="검색어 지우기"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* 표. 전체 폭 배치로 양옆 테두리 없이 위아래 hairline만 둔다. bare면 Card가 바깥 틀이다. */}
      <div className={cn(!bare && "border-y border-separator")}>
        {/* 셀은 줄을 바꾸지 않아서 `truncate` 글자의 최소 폭이 글 전체 길이가 된다. 긴 글 하나가 표를 카드보다 넓히면
            마지막 열(버튼)이 가로 스크롤 뒤로 밀린다. 그래서 셀 안의 `truncate` 글자는 표 영역 폭의 40%를 넘지 않게 하고,
            넘는 글은 말줄임표로 자른다. 쌓기 배치(좁은 화면)는 값 영역이 이미 남은 폭에 맞춰 자르므로 넓은 화면에만 건다. */}
        <div className="@container overflow-x-auto">
          <table
            className={cn(
              "w-full text-callout",
              stack ? "sm:[&_td_.truncate]:max-w-[40cqi]" : "[&_td_.truncate]:max-w-[40cqi]",
              stack && STACK.table,
            )}
          >
            {/* 평평한 머리글. 채운 띠 대신 아래 hairline과 작은 회색 라벨을 쓴다.
                sticky 가림용 배경은 페이지 배경과 같아서 눈에는 투명하게 보인다. */}
            <thead
              className={cn(
                "border-b border-separator bg-canvas-2 text-caption font-semibold text-label-2",
                !showHead && "hidden",
                stack && STACK.head,
              )}
            >
              <tr>
                {rowAccent && (
                  <th scope="col" className="w-[3px] p-0" aria-hidden="true" />
                )}
                {/* <th>는 UA 기본값이 center라서 본문 체크박스와 4px 어긋나므로 text-left를 준다. */}
                {(selectable || reserveSelection) && (
                  <th
                    scope="col"
                    className={cn("w-9", pad.head, "pr-0", "text-left")}
                  >
                    {selectable && pickable.length > 0 && (
                      <Checkbox
                        checked={allPicked}
                        onChange={toggleAll}
                        aria-label="이 페이지에서 선택 가능한 항목 전체 선택"
                      />
                    )}
                  </th>
                )}
                {columns.map((c, i) => {
                  const sortable = c.sortable !== false && !!c.value;
                  const active = sort?.i === i;
                  const caret = (
                    <span
                      className={cn(
                        "inline-flex transition-opacity",
                        active
                          ? "text-accent opacity-100"
                          : "opacity-0 group-hover:opacity-50",
                      )}
                      aria-hidden="true"
                    >
                      {active && sort!.dir === "asc" ? (
                        <ChevronUp size={12} />
                      ) : (
                        <ChevronDown size={12} />
                      )}
                    </span>
                  );
                  return (
                    <th
                      key={i}
                      scope="col"
                      style={{ width: c.width }}
                      className={cn(
                        pad.head,
                        // 본문 <td>와 같이 <th>에도 align-middle을 준다. 없으면 아이콘이 있는 열
                        // (inline-flex의 baseline이 아이콘 기준으로 잡힌다)과 없는 열의 라벨이 세로로 어긋난다.
                        "select-none whitespace-nowrap align-middle",
                        c.align === "right" ? "text-right" : "text-left",
                        sortable && "group cursor-pointer hover:bg-fill-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
                      )}
                      onClick={sortable ? () => toggle(i) : undefined}
                      /*
                        정렬 머리는 키보드로도 쓴다. Enter·Space로 정렬하고, 현재 방향은 `aria-sort`로 알린다.
                        `onClick`만 두면 키보드 사용자는 정렬할 수 없고 스크린리더는 정렬 방향을 읽지 못한다.
                      */
                      tabIndex={sortable ? 0 : undefined}
                      aria-sort={sortable ? (active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none") : undefined}
                      onKeyDown={
                        sortable
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                toggle(i);
                              }
                            }
                          : undefined
                      }
                    >
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 align-middle",
                          c.align === "right" && "justify-end",
                        )}
                      >
                        {/*
                          정렬 캐럿은 hover·정렬 때 라벨이 움직이지 않도록 항상 공간을 차지한다.
                          그래서 오른쪽 정렬 열에서는 캐럿을 라벨 앞에 둔다. 뒤에 두면 캐럿과
                          간격 19px만큼 라벨이 값보다 왼쪽에서 끝나 머리글과 숫자가 어긋난다.
                        */}
                        {sortable && c.align === "right" && caret}
                        {c.define ? (
                          <DefinedTerm define={c.define}>{c.label}</DefinedTerm>
                        ) : (
                          c.label
                        )}
                        {sortable && c.align !== "right" && caret}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody ref={bodyRef} className={cn("divide-y divide-separator", stack && STACK.body)}>
              {loading ? (
                Array.from({ length: SKELETON_ROWS }).map((_, ri) => (
                  <tr key={ri} aria-hidden="true">
                    {Array.from({ length: leadCols }).map((_, li) => (
                      <td key={`lead-${li}`} className="p-0" />
                    ))}
                    {columns.map((c, i) => (
                        <td
                          key={i}
                          className={cn(
                            columnRules &&
                              "border-r border-separator/50 last:border-r-0",
                            pad.cell,
                            c.align === "right" && "text-right",
                          )}
                        >
                          <Skeleton
                            className={cn(
                              "h-4",
                              i === 0 ? "w-3/5" : "w-[45%]",
                              c.align === "right" && "ml-auto",
                            )}
                          />
                        </td>
                    ))}
                  </tr>
                ))
              ) : error ? (
                /*
                  실패와 빈 상태를 표에서 나눈다. 그러면 표를 쓰는 모든 화면이 따로 판단하지 않고
                  `error={q.isError}` 한 줄로 같은 처리를 받는다.
                */
                <tr>
                  <td colSpan={shownCount + leadCols} className="p-0">
                    <ErrorState
                      message="목록 조회 실패"
                      onRetry={onRetry}
                      error={typeof error === "boolean" ? undefined : error}
                    />
                  </td>
                </tr>
              ) : view.length === 0 ? (
                <tr>
                  <td colSpan={shownCount + leadCols} className="p-0">
                    {/*
                      호출부의 `empty`는 아무것도 없을 때의 문구다("아직 작업 기록이 없습니다").
                      검색 때문에 0행인데 그 문구를 쓰면 기록이 3건 있는 화면이 "없습니다"로 잘못 표시되고 검색어를 지울 방법도 없다.
                      그래서 검색어가 있으면 지우기 버튼이 있는 `DTEmpty`를 쓴다.
                    */}
                    {!hasQuery && empty ? (
                      empty
                    ) : (
                      <DTEmpty
                        hasQuery={hasQuery}
                        onClear={handleClearAll}
                      />
                    )}
                  </td>
                </tr>
              ) : (
                pageRows.map((r, ri) => {
                  const isSel = !!selected?.has(rowKey(r));
                  const canPick = selectable?.(r) ?? false;
                  const group = rowGroupHeader?.(r, ri > 0 ? pageRows[ri - 1] : undefined);
                  return (
                    <Fragment key={rowKey(r)}>
                    {group != null && (
                      <tr className={cn("bg-canvas-2", stack && STACK.row)}>
                        <th
                          scope="colgroup"
                          colSpan={shownCount + leadCols}
                          className={cn(pad.head, "text-left text-caption font-semibold text-label")}
                        >
                          {group}
                        </th>
                      </tr>
                    )}
                    <tr
                      data-row-idx={onRowClick ? ri : undefined}
                      /*
                      행 안의 버튼·링크·체크박스를 누른 경우는 행 클릭에서 뺀다.
                      빼지 않으면 '재투입'을 한 번 눌렀을 때 재투입 확인과 미리보기가 함께 열린다.
                    */
                      onClick={
                        onRowClick
                          ? (e) => {
                              if (
                                (e.target as HTMLElement).closest(
                                  "button,a,input,select,label",
                                )
                              )
                                return;
                              onRowClick(r);
                            }
                          : undefined
                      }
                      onKeyDown={
                        onRowClick
                          ? (e) => {
                              /*
                              행 안의 요소에서 온 키 입력은 행의 입력에서 뺀다. 마우스 쪽 `onClick`이 `button,a,input…`을 걸러 내는 것과 같다.
                              빼지 않으면 행 안 버튼에서 Enter를 누를 때 그 버튼과 행이 함께 동작한다.
                              방향키는 행에 포커스가 있을 때만 처리한다.
                            */
                              if (e.target !== e.currentTarget) return;
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                onRowClick(r);
                                return;
                              }
                              if (e.key === "ArrowDown") {
                                e.preventDefault();
                                moveRow(ri + 1);
                              } else if (e.key === "ArrowUp") {
                                e.preventDefault();
                                moveRow(ri - 1);
                              } else if (e.key === "Home") {
                                e.preventDefault();
                                moveRow(0);
                              } else if (e.key === "End") {
                                e.preventDefault();
                                moveRow(pageRows.length - 1);
                              }
                            }
                          : undefined
                      }
                      /* 마우스로 고른 행이 다음 탭 정지가 된다. 사용자가 눌렀던 곳에서 이어진다. */
                      onFocus={onRowClick ? () => setActiveRow(ri) : undefined}
                      tabIndex={
                        onRowClick ? (ri === activeRow ? 0 : -1) : undefined
                      }
                      role={onRowClick ? "button" : undefined}
                      className={cn(
                        // `group`: 행 액션(제거 등)을 그 행에 마우스가 올라왔을 때만 보이게 하는 부모 클래스다.
                        // 파괴적 액션이 스무 행에 항상 떠 있으면 읽어야 할 값이 그 사이에 묻힌다.
                        "group transition-colors hover:bg-fill-3",
                        onRowClick &&
                          "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
                        // 고른 행은 지금 봐야 할 곳이라서 호박색 옅은 면으로 그린다.
                        isSel && "bg-amber-soft hover:bg-amber-soft",
                        stack && STACK.row,
                        rowClassName?.(r),
                      )}
                    >
                      {rowAccent && (
                        <td className="w-[3px] p-0">
                          <span
                            className={cn(
                              "block h-full min-h-[1.5rem] w-[3px]",
                              rowAccent(r),
                            )}
                          />
                        </td>
                      )}
                      {(selectable || reserveSelection) && (
                        <td
                          className={cn("w-9", pad.cell, "pr-0")}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {selectable && canPick && (
                            <Checkbox
                              checked={isSel}
                              onChange={() => toggleRow(r)}
                              aria-label={rowLabel ? `${rowLabel(r)} 선택` : `${ri + 1}번째 행 선택`}
                            />
                          )}
                        </td>
                      )}
                      {columns.map((c, i) => (
                          <td
                            key={i}
                            data-label={stack ? c.label : undefined}
                            className={cn(
                              "align-middle whitespace-nowrap text-label-2",
                              columnRules &&
                                "border-r border-separator/50 last:border-r-0",
                              pad.cell,
                              c.align === "right" && "text-right",
                              // 수와 날짜는 자릿수가 맞아야 열로 읽힌다. 열이 선언한 종류(kind)로 정하므로
                              // 호출부가 셀마다 다시 쓰지 않아도 된다.
                              (c.kind === "number" || c.kind === "date") &&
                                "tabular-nums",
                              c.mono && "font-mono tabular-nums",
                              c.tone === "muted" && "text-caption text-label-3",
                              c.tone === "faint" && "text-caption text-label-4",
                              stack && STACK.cell,
                            )}
                          >
                            {c.shrink ? (
                              <div className="grid grid-cols-[minmax(0,auto)]">
                                {c.cell ? c.cell(r) : c.value ? c.value(r) : null}
                              </div>
                            ) : c.cell ? c.cell(r) : c.value ? c.value(r) : null}
                          </td>
                      ))}
                    </tr>
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {/* 서버 커서 표의 하단이다. 내장 페이지네이션 대신 호출부가 '표시/전체 + 더 보기'를 넣는다.
            보기 도구가 있으면 같은 줄 오른쪽에 둔다(빈 띠를 새로 만들지 않는다). */}
        {(footer || viewTools) && !paged && view.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 border-t border-separator px-4 py-2.5 text-footnote text-label-3">
            <div className="flex flex-1 flex-wrap items-center justify-center gap-3">
              {footer}
            </div>
            {viewTools}
          </div>
        )}
        {paged && view.length > 0 && (
          /*
            폭이 좁아지면 줄을 바꾼다. `flex-wrap` 없이 두면 폭이 줄 때 자식이 줄어들고,
            body의 `overflow-wrap: anywhere` 때문에 글자마다 끊겨 세로로 늘어선다.
            자식은 `whitespace-nowrap`으로 자기 폭을 유지하고, 폭이 모자라면 래퍼가 줄을 하나 더 쓴다.
          */
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-separator px-4 py-2.5 text-footnote text-label-3">
            {/* 분모 줄. 규칙은 `tableCountLabel`에 있다. 같은 수를 두 번 쓰지 않고, 여러 페이지면 범위를 쓴다. */}
            <span className="whitespace-nowrap tabular-nums">
              {tableCountLabel({
                base: total ?? rows.length,
                matched: view.length,
                from: (curPage - 1) * rowsPerPage + 1,
                to: (curPage - 1) * rowsPerPage + pageRows.length,
                unit,
              })}
            </span>
            {/*
              안쪽에도 `flex-wrap`을 준다. 바깥에만 주면 이 묶음이 한 덩어리라서 보기 도구와 페이지 컨트롤이
              좁은 폭에서도 나란히 남는다. 그러면 375px 폭에서 `이전 1/1 다음`이 화면 밖으로 나가 가로 스크롤이 생긴다.
            */}
            <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1.5">
              {viewTools}
              {/* 페이지 컨트롤은 페이지가 두 개 이상일 때만 그린다. 한 페이지면 누를 수 없는 이전·다음과 1 / 1만 남는다. */}
              {pageCount > 1 && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={curPage <= 1}
                  className="whitespace-nowrap rounded-control px-2.5 py-1 font-medium text-label-2 transition-colors hover:bg-fill disabled:cursor-not-allowed disabled:text-label-4 disabled:hover:bg-transparent"
                >
                  이전
                </button>
                <span className="whitespace-nowrap px-1.5 tabular-nums text-label">
                  {curPage} / {pageCount}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                  disabled={curPage >= pageCount}
                  className="whitespace-nowrap rounded-control px-2.5 py-1 font-medium text-label-2 transition-colors hover:bg-fill disabled:cursor-not-allowed disabled:text-label-4 disabled:hover:bg-transparent"
                >
                  다음
                </button>
              </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
