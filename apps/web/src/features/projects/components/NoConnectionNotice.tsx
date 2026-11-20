/**
 * 고를 연결이 하나도 없을 때 분석 연동 카드와 프로젝트 추가 화면에 띄우는 안내.
 *
 * 저장소·분석 자격증명은 자격증명 연결에 등록한 것만 쓴다. 미리 채워 두는 기본 연결이 없으므로
 * 고를 것이 없으면 어디서 등록하는지를 같은 위치에서 알린다(결과와 다음 할 일을 두 줄로).
 * 자격증명 연결 화면은 최고관리자만 열 수 있다. 그 밖의 사람에게는 링크 대신 누가 하는지를 쓴다.
 */
import { Link } from "react-router-dom";

import { TargetNotice } from "./TargetNotice";

/** 연결 종류별 이름. `storage`는 NCP 저장소 연결, `analysis`는 분석(MI) 연결이다. */
const KIND_LABEL: Record<NoConnectionKind, string> = {
  storage: "NCP 저장소 연결",
  analysis: "분석 연결",
  both: "NCP 저장소 연결과 분석 연결",
};

/** 안내가 가리키는 연결 종류. */
export type NoConnectionKind = "storage" | "analysis" | "both";

/** 안내의 두 줄 문장을 만든다. 화면이 같은 문장을 쓰도록 테스트가 이 함수를 검사한다. */
export function noConnectionText(kind: NoConnectionKind, canManage: boolean): {
  title: string; body: string;
} {
  const label = KIND_LABEL[kind];
  return {
    title: `고를 수 있는 ${label}이 없습니다`,
    body: canManage
      ? `자격증명 연결에서 ${label}을 먼저 등록하고 점검합니다.`
      : `최고관리자가 자격증명 연결에서 ${label}을 등록하면 고를 수 있습니다.`,
  };
}

/** 고를 연결이 없다는 안내를 그린다. 최고관리자에게는 자격증명 연결로 가는 링크를 붙인다. */
export function NoConnectionNotice({
  kind, canManage,
}: { kind: NoConnectionKind; canManage: boolean }) {
  const t = noConnectionText(kind, canManage);
  return (
    <TargetNotice tone="warn" title={t.title}>
      {t.body}
      {canManage && (
        <>
          {" "}
          <Link
            to="/admin/connections"
            className="font-semibold text-accent underline-offset-2 hover:underline"
          >
            자격증명 연결 열기
          </Link>
        </>
      )}
    </TargetNotice>
  );
}
