/**
 * 통계 화면 위 요약 칸의 값을 고르는 규칙. 서버가 준 프로젝트 합계를 읽기만 한다.
 *
 * 합계와 비율, 분모는 서버(`analytics.dashboard.labeler_summary`)가 사람별 행과 같은 규칙으로 낸다.
 * 이 모듈은 칸마다 큰 값, 단위, 아래 줄, 정의 문구를 고르고 나눗셈은 하지 않는다.
 * 비율 칸은 사람 표와 같은 함수(`approvalView`·`rejectionView`·`editRateView`)를 써서 `측정 전`과 `—`를 같은 조건으로 고른다.
 * 요약 칸이 따로 고르면 같은 화면의 위아래가 같은 상태를 다른 말로 부른다.
 */
import type { LabelerPerfSummary } from "@/shared/api/client";

import { editRateFraction, editRateView } from "./edit_rate";
import { metricDefine, METRICS } from "./metrics";
import { approvalView, rejectionView, splitUnit } from "./rates";

/** 요약 칸 하나. `unit`은 큰 값 옆에 작게 쓰고, 값이 `측정 전`·`—`이면 비운다. */
export interface SummaryCell {
  key: "submitted" | "decided" | "approvalRate" | "rejectionRate" | "editRate";
  name: string;
  value: string;
  unit?: string;
  sub: string;
  define: string;
}

/** 요약 칸 정의 문구 끝에 붙이는 문장. 위 칸과 아래 표가 같은 수라는 것을 밝힌다. */
const SUM_NOTE = "아래 사람별 표의 수를 더한 값과 같습니다.";

/**
 * 프로젝트 합계로 요약 칸 다섯 개를 만든다.
 *
 * Args:
 *   s: 서버가 낸 기간 합계.
 *   period: 기간 이름(`최근 30일` 등). 정의 문구에 넣는다.
 *
 * Returns:
 *   제출, 판정, 승인율, 반려율, 수정률 순서의 칸.
 */
export function summaryCells(s: LabelerPerfSummary, period: string): SummaryCell[] {
  const n = (v: number) => v.toLocaleString("ko-KR");
  const approval = approvalView(s);
  const rejection = rejectionView(s);
  const edit = editRateView(s);
  const [apprValue, apprUnit] = splitUnit(approval.text);
  const [rejValue, rejUnit] = splitUnit(rejection.text);
  const [editValue, editUnit] = splitUnit(edit.text);
  return [
    {
      key: "submitted",
      name: METRICS.submitted.name,
      value: n(s.submitted),
      unit: "건",
      sub: s.submitters > 0 ? `제출한 사람 ${n(s.submitters)}명` : "제출 없음",
      define: metricDefine(METRICS.submitted, `${period} 안의 프로젝트 전체 제출을 셉니다.`, SUM_NOTE),
    },
    {
      /* 판정 수는 승인율의 분모(`approval_base`)와 같은 값이다. 서버가 낸 분모를 그대로 써서 승인율 괄호와 어긋나지 않게 한다. */
      key: "decided",
      name: METRICS.decided.name,
      value: n(s.approval_base),
      unit: "건",
      sub: s.approval_base > 0 ? `승인 ${n(s.approved)} · 반려 ${n(s.rejected)}` : "판정 없음",
      define: metricDefine(METRICS.decided, `${period} 안에 내린 판정을 셉니다.`, SUM_NOTE),
    },
    {
      key: "approvalRate",
      name: METRICS.approvalRate.name,
      value: apprValue,
      unit: apprUnit,
      sub: approval.sub,
      define: metricDefine(
        METRICS.approvalRate,
        `${period} 안의 판정을 프로젝트 전체로 합쳐 셉니다.`,
        "값 아래 괄호는 승인 수와 판정 수입니다.",
      ),
    },
    {
      key: "rejectionRate",
      name: METRICS.rejectionRate.name,
      value: rejValue,
      unit: rejUnit,
      sub: rejection.sub,
      define: metricDefine(
        METRICS.rejectionRate,
        `${period} 안의 제출과 반려를 프로젝트 전체로 합쳐 셉니다.`,
        "값 아래 괄호는 반려 수와 제출 수이고, 판정이 제출보다 많으면 판정 수입니다.",
      ),
    },
    {
      key: "editRate",
      name: METRICS.editRate.name,
      value: editValue,
      unit: editUnit,
      sub:
        edit.kind === "rate"
          ? editRateFraction(s)
          : edit.kind === "unmeasured"
            ? "측정한 제출 없음"
            : "제출 없음",
      define: metricDefine(
        METRICS.editRate,
        `${period} 안의 제출을 프로젝트 전체로 합쳐 셉니다.`,
        "교정한 글자의 합계를 AI 초안 글자의 합계로 나눕니다.",
        "값 아래 괄호는 교정한 글자와 AI 초안 글자입니다.",
      ),
    },
  ];
}
