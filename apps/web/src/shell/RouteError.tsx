/**
 * 라우팅이 실패했을 때 사용자에게 보여 주는 화면(`errorElement`).
 *
 * 없는 주소는 돌아가면 되고, 나머지는 다시 시도하거나 관리자에게 알려야 한다.
 * 사용자가 할 일이 달라서 404(없는 주소)와 그 밖의 오류를 나눈다. 한 문구로 합치면 두 경우 모두 틀린 안내가 된다.
 *
 * 돌아갈 곳은 모든 역할이 여는 프로젝트 목록이라, 역할 정보가 없어도 틀린 곳을 가리키지 않는다.
 * 버튼 이름은 목적지 이름으로 쓴다. "홈"은 화면 어디에도 없는 말이다.
 * "작업으로" 같은 목적지를 새로 만들면 역할에 따라 없는 화면을 가리키게 된다.
 */
import { useState } from "react";
import { Compass, TriangleAlert } from "lucide-react";
import { Link, isRouteErrorResponse, useLocation, useRouteError } from "react-router-dom";

import { Button, buttonClasses } from "@/shared/ui/Button";
import { StatePanel } from "@/shared/ui/StatePanel";
import { moveLabel, PROJECT_LIST_LANDING } from "@/lib/roles";
import { fmtDateTime } from "@/shared/lib/time";

import { UNKNOWN_LABEL } from "./breadcrumbs";

/** 프로젝트 목록으로 가는 링크를 그린다. 접근 안내(`AccessDenied`)가 역할을 모를 때 보내는 곳과 같다. */
function ListLink({ variant }: { variant: "primary" | "secondary" }) {
  return (
    <Link to={PROJECT_LIST_LANDING.to} className={buttonClasses(variant)}>
      {moveLabel(PROJECT_LIST_LANDING.label)}
    </Link>
  );
}

/**
 * 라우팅 오류 화면을 그린다. 404면 없는 주소 안내를, 그 밖에는 실패 안내를 보여 준다.
 *
 * @param notFound 이 화면을 404로 고정하면 true. 캐치올 라우트(`path: "*"`)가 쓴다.
 *
 * 캐치올은 `errorElement` 대신 일반 element라서 `useRouteError()`가 `undefined`다.
 * 그래서 자동 판정만으로는 없는 주소를 실패로 안내하게 되므로, 부르는 쪽이 `notFound`로 넘긴다.
 */
export function RouteError({ notFound: forced = false }: { notFound?: boolean } = {}) {
  const error = useRouteError();
  const { pathname } = useLocation();
  // 오류 시각은 처음 그릴 때 한 번 정한다. 관리자에게 알릴 때 서버 로그와 대조하는 기준이다.
  const [at] = useState(() => new Date().toISOString());
  const notFound = forced || (isRouteErrorResponse(error) && error.status === 404);

  if (notFound) {
    return (
      <StatePanel
        icon={<Compass size={18} aria-hidden="true" />}
        // 브레드크럼의 끝 마디와 같은 이름을 써서 위치 표시와 본문 제목이 한 화면을 같은 이름으로 부르게 한다.
        title={UNKNOWN_LABEL}
        lead="이 주소에 맞는 화면이 없습니다."
        hint="주소를 잘못 적었거나 삭제된 화면입니다."
        facts={[{ label: "요청한 주소", value: pathname, mono: true }]}
        actions={<ListLink variant="primary" />}
      />
    );
  }

  // 오류 원문은 사용자에게 의미가 없고 스택이 섞여 나올 수 있으므로 화면 대신 콘솔에만 남긴다.
  if (error) console.error("[route]", error);

  // 다시 시도는 새로고침이다. 배포 중 청크 교체나 네트워크 같은 일시 오류는 대개 새로 받으면 해결된다.
  return (
    <StatePanel
      tone="bad"
      icon={<TriangleAlert size={18} aria-hidden="true" />}
      title="화면 열기 실패"
      lead="잠시 후 다시 시도해주세요."
      hint="계속되면 관리자에게 오류 시각을 알려주세요."
      facts={[{ label: "오류 시각", value: fmtDateTime(at), mono: true }]}
      actions={
        <>
          <Button onClick={() => window.location.reload()}>다시 시도</Button>
          <ListLink variant="secondary" />
        </>
      }
    />
  );
}
