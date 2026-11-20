/**
 * 진척률의 표시 이름. 판정은 서버가 하고, 이름은 이 파일 한 곳에서 붙인다.
 *
 * 계산은 서버 함수 하나가 한다(`analytics.stats.progress_pct`·`hours_progress`).
 * 이 파일은 어떤 기준으로 쟀는지를 화면마다 같은 이름으로 부르게 한다.
 * 화면마다 이름을 적으면 개요는 `전체 진척`, 설정은 `가공 완료율`처럼 다시 달라진다.
 * 같은 프로젝트의 진척이 기준에 따라 건수·시간으로 다르게 나오므로 이름이 기준을 밝힌다.
 *
 * 나눗셈을 이 파일에 두면 서버와 다른 판정이 하나 더 생기므로 이 파일에는 계산을 두지 않는다.
 *
 * 표시 분기는 이 파일에 있다. 어느 수를 큰 글씨로 낼지, 어떤 기준을 밝힐지,
 * 무엇을 그리지 않을지를 `progressView` 한 곳에서 정한다.
 * 그리는 것은 개요의 진척률 카드(`features/insights/components/ManagerCards`)다.
 */
import { fmtH } from "@/shared/lib/fmtSecs";
import type { BusinessKpi, HoursProgress } from "@/shared/api/client";

/** 지표 이름. 진척률을 보여 주는 화면은 이 이름을 쓴다. */
export const PROGRESS_LABEL = "진척률";

/**
 * 시간 진척 기준의 화면 이름. 이 저장소에서 `보유`는 지금 누가 맡은 작업을 뜻하므로 기준 이름에 쓰지 않는다.
 */
export const HOURS_BASIS_LABEL: Record<HoursProgress["basis"], string> = {
  target: "계약 목표",
  // 이 기준의 분모는 클립 길이의 합이다.
  // `등록 분량`이라고 쓰면 영상 등록으로 읽혀 클립이 없는 영상의 분량과 헷갈린다.
  intake: "클립 분량",
};

/**
 * 시간 진척의 정의 문구. 기준마다 분모가 다르다.
 *
 * 삭제한 영상은 분모에서 빠진다. 영상을 지운 뒤 분모가 줄어든 것을 보고 "수가 틀렸다"고 읽기 쉬워서
 * 이 사실을 정의에 적는다. 삭제한 영상의 승인분은 납품에 남으므로 예외로 분자와 분모에 남긴다.
 * 정의는 뜻을 먼저 쓰고 계산식은 괄호로 뒤에 붙인다. `분모` 같은 통계 용어는 풀어 쓴다.
 */
export const HOURS_BASIS_DEFINE: Record<HoursProgress["basis"], string> = {
  target:
    "계약 목표 시간 중 승인까지 끝난 분량의 비율입니다. 아직 등록하지 않은 분량도 목표에 포함됩니다. " +
    "(승인된 작업의 클립 길이 합 ÷ 목표 시간)",
  intake:
    "등록한 클립 분량 중 승인까지 끝난 분량의 비율입니다. 비식별화 신고로 제외한 클립과 삭제한 영상의 클립은 빼고, 삭제한 영상의 승인분은 남깁니다. " +
    "목표 시간을 설정하면 목표 대비로 바뀝니다. (승인된 작업의 클립 길이 합 ÷ 클립 길이 합)",
};

/** 건수 진척 기준의 이름. 시간 진척과 다른 수라는 것을 이름으로 구분한다. */
export const COUNT_BASIS_LABEL = "건수 기준";

/** 건수 진척의 정의 문구. 서버 `progress_pct`(승인 수를 사람 작업 수로 나눈 값, 소수 1자리)를 문장으로 옮긴다. */
export const COUNT_BASIS_DEFINE =
  "전체 작업 중 승인된 작업의 비율입니다. 아직 시작하지 않은 작업도 포함하고, 분석을 기다리는 영상과 " +
  "종료된 작업은 제외합니다. 시간으로 잰 진척과는 값이 다릅니다. (승인 ÷ 전체 작업)";

/**
 * 작업 수의 모집단 정의 문구. 큐 깊이·작업 수가 무엇을 세는지 알린다.
 *
 * 비식별화 신고로 제외됐거나 영상이 삭제되어 닫힌 작업은 세지 않는다.
 * 이 조건을 밝히지 않으면 영상을 지운 다음 날 `작업 272`가 `작업 261`이 된 것을 보고 데이터가 사라졌다고 읽는다.
 */
export const WORK_POPULATION_DEFINE =
  "클립 단위의 라벨링·검수 작업 수입니다. 분석을 기다리는 영상과, 비식별화 신고 제외·영상 삭제로 종료된 작업은 세지 않습니다.";

/**
 * 진척률 표시 하나를 그리는 데 필요한 값. 개요와 통계가 같은 판정을 쓴다.
 *
 * 계산은 서버가 한다.
 * 이 타입은 어느 수를 큰 글씨로 낼지, 어떤 기준을 밝힐지, 무엇을 그리지 않을지의 분기 결과를 담는다.
 */
export interface ProgressView {
  /** 큰 글씨로 보여 줄 수 하나. 잴 것이 아예 없으면 0% 대신 `—`다. */
  headline: string;
  /** 큰 수 아래 한 줄. 어떤 기준으로 쟀는지 보여 준다. 기준을 밝힐 수 없는 상태면 `null`. */
  basis: { label: string; define: string; detail: string } | null;
  /** 기준을 밝힐 수 없는 이유 한 줄(측정 전·클립 없음). 없으면 `null`. */
  note: string | null;
  /** 아래 줄의 건수 진척. 시간으로 쟀을 때만 둔다(시간이 없으면 큰 수가 이미 건수다). */
  count: { pct: string; done: number; total: number } | null;
  /** 클립이 없어 시간으로 재지 못한 건수. 0이면 `null`. */
  hoursUnmeasurable: number | null;
}

/**
 * 서버 응답 한 건을 진척률 표시 값으로 바꾼다. 판정 규칙은 이 함수 한 곳에 둔다.
 *
 * 관리자 개요와 검수자 개요의 진척률 카드가 같은 응답에서 같은 것을 그리게 한다.
 * 시간으로 재지 못한 건수가 있을 때, 측정 전일 때의 표시를 여기서 한 번만 정한다.
 *
 * Args:
 *   d: `/projects/{id}/dashboard/business-kpi` 응답. 아직 없으면 `undefined`.
 *
 * Returns:
 *   그릴 값만 담은 객체. 그리지 않을 항목은 `null`이라 호출부에 조건문이 생기지 않는다.
 */
export function progressView(d?: BusinessKpi): ProgressView {
  const empty: ProgressView = {
    headline: "—",
    basis: null,
    note: null,
    count: null,
    hoursUnmeasurable: null,
  };
  if (d == null) return empty;

  const hoursUnmeasurable = d.hours_unmeasurable_count > 0 ? d.hours_unmeasurable_count : null;
  const hp = d.hours_progress ?? null;

  // 시간으로 잴 수 있으면 시간 진척이 큰 수다. 계약 단위가 시간이다.
  if (hp != null) {
    return {
      headline: `${hp.pct}%`,
      basis: {
        label: HOURS_BASIS_LABEL[hp.basis],
        define: HOURS_BASIS_DEFINE[hp.basis],
        detail: `${fmtH(d.done_hours)} / ${fmtH(hp.scale_hours)}`,
      },
      note: null,
      // 작업이 0건이면 `0% · 0 / 0건`은 의미 없는 수라서 줄을 그리지 않는다.
      count:
        d.total_count > 0
          ? { pct: `${d.progress_pct}%`, done: d.done_count, total: d.total_count }
          : null,
      hoursUnmeasurable,
    };
  }

  // 작업이 한 건도 없으면 `%`를 그리지 않는다. 서버는 분모가 0일 때 `progress_pct`를 `0.0`으로 준다.
  // 큰 글씨 `0%`는 "아무것도 끝내지 못했다"로 읽히지만, 실제로는 아직 셀 것이 없는 상태다.
  if (d.total_count === 0) {
    return { ...empty, note: "진척 측정 전", hoursUnmeasurable };
  }

  // 클립이 아직 없어 시간 분모가 0이다. 건수로 표시하고 건수 기준이라고 밝힌다.
  // 큰 수가 이미 건수 진척이라 아래 건수 줄은 두지 않는다. 두면 같은 %를 두 번 보여 준다.
  return {
    headline: `${d.progress_pct}%`,
    basis: {
      label: COUNT_BASIS_LABEL,
      define: COUNT_BASIS_DEFINE,
      detail: `${d.done_count.toLocaleString("ko-KR")} / ${d.total_count.toLocaleString("ko-KR")}건`,
    },
    note: "클립이 없어 시간 대신 건수로 집계합니다",
    count: null,
    hoursUnmeasurable,
  };
}
