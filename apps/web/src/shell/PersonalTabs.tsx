/**
 * 개인 표면 탭. `내 작업 · 내 활동` 두 화면을 한 곳에서 오간다.
 *
 * 이 둘은 프로젝트를 바꿔도 항목이 바뀌지 않는 개인 기준 화면이다.
 * 사이드바는 `내 작업공간` 한 항목으로 합치고, 두 화면 사이 이동은 이 탭이 맡는다.
 * 중첩 라우트 대신 각 페이지가 이 탭 줄을 직접 넣는다.
 *
 * 둘은 화면 안의 패널이 아닌 서로 다른 주소다. 그래서 `role="tab"` 버튼 대신 `<nav>` 안의
 * 링크로 그리고, 지금 화면에 `aria-current="page"`를 단다. 새 탭으로 열 수 있고 스크린리더는
 * 페이지 이동으로 읽는다. 모양은 `ui/Tabs`의 밑줄 탭과 같다.
 */
import { Link, useLocation } from "react-router-dom";

import { cn } from "@/shared/lib/cn";

import { PERSONAL_LABEL, PERSONAL_PATHS } from "./nav-config";

/** `ui/Tabs` underline 변형과 같은 치수. 바꾸면 두 곳을 함께 고친다. */
const TAB =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap -mb-px border-b-2 px-4 py-2.5 text-callout font-medium transition-[color,border-color] duration-short focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus";

/** 개인 표면 탭 줄을 그린다. */
export function PersonalTabs() {
  const { pathname } = useLocation();

  // 정확히 일치하는 링크가 없으면(하위 경로 등) 어느 링크도 현재로 두지 않는다.
  return (
    // 스크롤은 바깥이 맡고 구분선·밑줄 겹침은 안쪽이 맡는다(`ui/Tabs`의 주석과 같은 이유).
    <nav aria-label="내 작업공간" className="overflow-x-auto">
      <ul className="flex w-max min-w-full items-center border-b border-separator">
        {PERSONAL_PATHS.map((k) => {
          const current = pathname === k;
          return (
            <li key={k}>
              <Link
                to={k}
                aria-current={current ? "page" : undefined}
                className={cn(
                  TAB,
                  current ? "border-accent font-semibold text-accent" : "border-transparent text-label-2 hover:text-label",
                )}
              >
                {PERSONAL_LABEL[k]}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
