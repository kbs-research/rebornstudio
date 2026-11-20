/**
 * `DataTable`의 분모 줄 문구와 표 상태(정렬·검색·페이지·행 수)를 주소에 두는 연결.
 *
 * 문구 규칙과 주소 읽기·쓰기는 순수 함수로 두어 렌더 없이 테스트한다(`table_state.test.ts`).
 */
import { useCallback, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";

/** 주소에 두는 표 상태. 열은 인덱스 대신 머리글 이름으로 가리킨다. */
export interface TableUrlState {
  /** 정렬 열 이름과 방향. 표의 처음 정렬과 같으면 `null`이다. */
  sort: { label: string; dir: "asc" | "desc" } | null;
  /** 검색어. */
  q: string;
  /** 보고 있는 페이지(1부터). */
  page: number;
  /** 페이지당 행 수. 표 기본값과 같으면 `null`이다. */
  rows: number | null;
}

/** `DataTable`의 `urlState`에 넘기는 연결. 처음 상태와 바뀐 상태를 받는 함수다. */
export interface TableUrlBinding {
  /** 주소에서 읽은 처음 상태. */
  initial: TableUrlState;
  /** 표 상태가 바뀌면 불린다. 같은 상태면 주소를 바꾸지 않는다. */
  onChange: (s: TableUrlState) => void;
}

/**
 * 분모 줄 문구를 만든다.
 *
 * 같은 수를 두 번 쓰지 않는다(`전체 10건 중 10건` 대신 `전체 10건`). 여러 페이지면 이 페이지의 범위를 쓴다.
 * 조건으로 거른 목록이면 전체 수를 분모로 함께 쓴다. 거른 수만 쓰면 그 수가 전체로 읽힌다.
 *
 * @example tableCountLabel({ base: 39, matched: 39, from: 1, to: 39, unit: "명" }) // "전체 39명"
 * @example tableCountLabel({ base: 39, matched: 39, from: 26, to: 39, unit: "명" }) // "39명 중 26–39"
 * @example tableCountLabel({ base: 13, matched: 6, from: 1, to: 6, unit: "개" }) // "13개 중 6개"
 */
export function tableCountLabel(o: { base: number; matched: number; from: number; to: number; unit: string }): string {
  const n = (x: number) => x.toLocaleString("ko-KR");
  const onePage = o.from <= 1 && o.to >= o.matched;
  const range = `${n(o.from)}–${n(o.to)}`;
  if (o.matched === o.base) {
    return onePage ? `전체 ${n(o.base)}${o.unit}` : `${n(o.base)}${o.unit} 중 ${range}`;
  }
  const head = `${n(o.base)}${o.unit} 중 ${n(o.matched)}${o.unit}`;
  return onePage ? head : `${head} · ${range}`;
}

/**
 * 보고 있는 페이지를 행 수에 맞춰 마지막 페이지 안으로 줄인다.
 *
 * 주소로 범위 밖 페이지(`?page=99`)를 열면 표는 마지막 페이지를 그리는데, 상태와 주소가 99로 남으면
 * 보이는 페이지와 주소가 다르고 `이전`을 눌러도 98이 되어 화면이 그대로다. 그래서 상태를 이 값으로 맞춘다.
 * 행이 없으면(아직 받기 전이거나 비었으면) 그대로 둔다. 받기 전에 주소의 페이지를 지우면 받은 뒤 그 페이지를 열 수 없다.
 * 페이지를 끈 표(`rowsPerPage` 0)도 그대로 둔다.
 */
export function clampPage(page: number, rowCount: number, rowsPerPage: number): number {
  if (rowsPerPage <= 0 || rowCount <= 0) return page;
  return Math.max(1, Math.min(page, Math.ceil(rowCount / rowsPerPage)));
}

/** 주소 키를 만든다. 접두어가 있으면 `접두어.이름`이다. */
const keyOf = (prefix: string, name: string) => (prefix ? `${prefix}.${name}` : name);

/**
 * 주소의 검색 매개변수에서 표 상태를 읽는다. 쓸 수 없는 값(음수 페이지, 정의되지 않은 방향)은 버린다.
 */
export function readTableUrlState(sp: URLSearchParams, prefix: string): TableUrlState {
  const sortLabel = sp.get(keyOf(prefix, "sort"));
  const dirRaw = sp.get(keyOf(prefix, "dir"));
  const dir = dirRaw === "desc" ? "desc" : "asc";
  const pageRaw = Number(sp.get(keyOf(prefix, "page")));
  const rowsRaw = Number(sp.get(keyOf(prefix, "rows")));
  return {
    sort: sortLabel ? { label: sortLabel, dir } : null,
    q: sp.get(keyOf(prefix, "q")) ?? "",
    page: Number.isInteger(pageRaw) && pageRaw > 1 ? pageRaw : 1,
    rows: [10, 25, 50, 100].includes(rowsRaw) ? rowsRaw : null,
  };
}

/**
 * 표 상태를 검색 매개변수에 적은 새 값을 돌려준다. 이 표의 키만 바꾸고 다른 키(탭·기간)는 둔다.
 */
export function writeTableUrlState(sp: URLSearchParams, prefix: string, s: TableUrlState): URLSearchParams {
  const next = new URLSearchParams(sp);
  const put = (name: string, v: string | null) => {
    if (v) next.set(keyOf(prefix, name), v);
    else next.delete(keyOf(prefix, name));
  };
  put("sort", s.sort?.label ?? null);
  put("dir", s.sort ? s.sort.dir : null);
  put("q", s.q.trim() ? s.q : null);
  put("page", s.page > 1 ? String(s.page) : null);
  put("rows", s.rows ? String(s.rows) : null);
  return next;
}

/**
 * 표 상태를 주소에 두는 연결을 만든다. `DataTable`의 `urlState`에 그대로 넘긴다.
 *
 * 처음 상태는 마운트 때 한 번 읽는다. 쓰기는 `replace`라 뒤로 가기 단계를 늘리지 않는다.
 * 같은 상태는 다시 적지 않는다. 적을 때마다 주소가 바뀌고 화면이 다시 그려져 효과가 또 불린다.
 *
 * Args:
 *   prefix: 한 화면에 표가 둘 이상일 때 키를 나누는 접두어. 하나면 빈 문자열이다.
 */
export function useTableUrlState(prefix = ""): TableUrlBinding {
  const [sp, setSp] = useSearchParams();
  const latest = useRef({ sp, setSp });
  latest.current = { sp, setSp };
  // 처음 주소만 읽는다. 표가 주소를 다시 읽으면 쓰기와 읽기가 서로를 계속 부른다.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const initial = useMemo(() => readTableUrlState(sp, prefix), [prefix]);
  const onChange = useCallback(
    (s: TableUrlState) => {
      const { sp: cur, setSp: set } = latest.current;
      const next = writeTableUrlState(cur, prefix, s);
      if (next.toString() !== cur.toString()) set(next, { replace: true });
    },
    [prefix],
  );
  return useMemo(() => ({ initial, onChange }), [initial, onChange]);
}
