/**
 * 확인 창과 사람 서랍이 쓰는 회색 사실 상자. 왼쪽 이름, 오른쪽 값을 한 줄씩 둔다.
 *
 * 확인 창은 모두 같은 틀을 쓴다. 대상이 든 한 문장, 이 상자(무엇이 어떻게 되는지),
 * 되돌리는 방법 한 줄 순서다. 사실을 문장 안에 넣으면 항목끼리 비교하기 어렵고 수가 눈에 띄지 않는다.
 */
import type { ReactNode } from "react";

/** 사실 한 줄 */
export interface Fact {
  label: string;
  value: ReactNode;
}

/** 사실 목록을 가라앉은 면에 그린다. */
export function FactBox({ facts }: { facts: Fact[] }) {
  return (
    <dl className="space-y-1.5 rounded-card bg-canvas-2 px-4 py-3 text-footnote">
      {facts.map((f) => (
        <div key={f.label} className="flex items-baseline justify-between gap-4">
          <dt className="text-label-3">{f.label}</dt>
          <dd className="text-right tabular-nums text-label">{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}
