/**
 * 개인 작업공간의 머리글. 표면 이름(h1), 프로젝트 칩, 오른쪽 액션, 개인 탭을 함께 그린다.
 *
 * 화면마다 `PageHeader`와 `PersonalTabs`를 따로 얹으면 브레드크럼·h1·활성 탭이 같은 이름을 세 번 쓰고
 * hairline도 두 개가 된다. 그래서 하나로 묶고, h1은 표면을, 탭은 페이지를 가리키며, 선은 하나만 둔다.
 *
 * 부제는 탭 이름이나 카드의 범위 칩을 되풀이하므로 두지 않는다.
 *
 * 지금 보고 있는 프로젝트를 머리글에 항상 표시한다.
 * `내 작업`·`내 활동`은 주소에 프로젝트가 없고 왼쪽 위 전환기 값을 따른다.
 * 칩이 없으면 다른 프로젝트를 가리키는 링크(`?project=`)로 `내 작업`에 왔을 때 맥락이 바뀐 것을 알 수 없다.
 */
import { PersonalProjectChip } from "./PersonalProjectChip";
import type { ReactNode } from "react";

import { PersonalTabs } from "./PersonalTabs";

/** 개인 작업공간 머리글을 그린다. `actions`는 오른쪽에 둔다. */
export function PersonalHeader({ actions }: { actions?: ReactNode }) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 pb-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <h1 className="min-w-0 text-title-1 text-label">
            내 작업공간
          </h1>
          <PersonalProjectChip />
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      <PersonalTabs />
    </header>
  );
}
