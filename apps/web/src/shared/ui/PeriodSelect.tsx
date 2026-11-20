/**
 * 모니터링 화면의 기간을 정하는 단일 출처. 화면 하나에 기간은 하나다.
 *
 * 누적값은 프로젝트가 오래될수록 최근 변화가 큰 분모에 묻혀 요즘 상황을 보여 주지 못한다.
 * 모든 지표가 전 기간 누적이면 어제 화면과 오늘 화면이 같다.
 *
 * 고른 값은 URL(`?period=`)에 두어 링크를 받은 사람이 같은 화면을 보게 한다. 프로젝트를 주소의 `/p/:slug`에 두는 것과 같은 이유다.
 * 상태에만 두면 사용자가 본 수를 다른 사람에게 보여 줄 방법이 없다.
 *
 * 주의: 큐 깊이·진행률처럼 현재 상태를 나타내는 수는 기간을 좁혀도 그대로이므로 화면에 `지금`이라고 표시한다.
 * 기간 컨트롤이 있는데 수가 바뀌지 않으면 사용자는 고장으로 받아들인다.
 */
import { useSearchParams } from "react-router-dom";

import { Select } from "@/shared/ui/Select";

/** 고를 수 있는 기간. `all`은 전 기간이며 서버에 `since`를 보내지 않는다. */
export type PeriodKey = "all" | "7d" | "30d" | "90d";

const OPTIONS: { key: PeriodKey; label: string; days: number | null }[] = [
  { key: "all", label: "전체 기간", days: null },
  { key: "7d", label: "최근 7일", days: 7 },
  { key: "30d", label: "최근 30일", days: 30 },
  { key: "90d", label: "최근 90일", days: 90 },
];

/** 기본 기간. 전 기간이라 기간이 없던 화면과 같은 수로 시작한다(확장-수축 방식). */
export const DEFAULT_PERIOD: PeriodKey = "all";

/** 문자열이 지원하는 기간 키이면 true를 돌려준다. */
function isPeriod(v: string | null): v is PeriodKey {
  return v != null && OPTIONS.some((o) => o.key === v);
}

/** 기간의 한국어 이름을 돌려준다. 표 헤더 정의나 빈 상태 문구에 그대로 넣는다. */
export function periodLabel(key: PeriodKey): string {
  return OPTIONS.find((o) => o.key === key)?.label ?? "전체 기간";
}

/**
 * 서버에 보낼 `since`(ISO 문자열)를 만든다. `all`이면 `undefined`를 돌려주어 파라미터를 보내지 않는다.
 *
 * 기간으로 자르는 일은 서버가 한다. 화면이 자르면 페이지에 실린 행만 센다.
 */
export function periodSince(key: PeriodKey): string | undefined {
  const days = OPTIONS.find((o) => o.key === key)?.days;
  if (days == null) return undefined;
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

/** URL(`?period=`)에서 현재 기간과 변경 함수를 얻는다. 지원하지 않는 값은 기본값으로 처리한다. */
export function usePeriod(): [PeriodKey, (v: PeriodKey) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("period");
  const value = isPeriod(raw) ? raw : DEFAULT_PERIOD;
  return [
    value,
    (v: PeriodKey) => {
      const next = new URLSearchParams(params);
      if (v === DEFAULT_PERIOD) next.delete("period");
      else next.set("period", v);
      // 기간 변경은 뒤로 가기로 되돌릴 단계가 아니므로 히스토리를 쌓지 않는다.
      setParams(next, { replace: true });
    },
  ];
}

/** 페이지 머리글 오른쪽에 두는 기간 선택을 그린다. 이 화면의 모든 기간 지표에 적용된다. */
export function PeriodSelect({ value, onChange }: { value: PeriodKey; onChange: (v: PeriodKey) => void }) {
  return (
    <Select
      aria-label="기간"
      value={value}
      onChange={(e) => onChange(e.target.value as PeriodKey)}
      className="h-8 text-footnote"
    >
      {OPTIONS.map((o) => (
        <option key={o.key} value={o.key}>{o.label}</option>
      ))}
    </Select>
  );
}
