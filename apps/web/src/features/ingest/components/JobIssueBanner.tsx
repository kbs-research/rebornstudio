/**
 * Job 상세 상단의 이슈 배너. 실패를 찾으러 스크롤하지 않도록 단계 구조보다 위에 실패 요약을 둔다.
 *
 * 발생 위치별로 나눠 보여 주어("검증 실패 3 · 변환 실패 1") 무엇부터 볼지 정하게 한다.
 * 나눈 수는 서버 집계(`prep_blocked_stage` GROUP BY)라서 화면이 세지 않는다.
 * 0건이면 그리지 않는다.
 */
import { TriangleAlert } from "lucide-react";

import { cn } from "@/shared/lib/cn";
import { blockedAtLabel, blockedHintOr, type PrepBlockedStage } from "../lib/vocab";

type Issue = { stage: PrepBlockedStage; count: number };

/**
 * 위치별 한 줄 설명. 무엇부터 조치할지 판단하는 근거다.
 * 옆의 `BLOCKED_AT`(검증, 분석본 변환 등)이 상태 이름을 보여 주므로 설명 앞에 상태 이름을 붙이지 않는다.
 *
 * 주의: 관리자에게 그대로 나가는 제품 문구다. `~합니다`로 끝나는 문장으로 쓰고,
 * 평문으로 그리므로 마크다운을 넣지 않는다.
 */
const HINT: Record<PrepBlockedStage, string> = {
  upload: "업로드가 끝나지 않았습니다.",
  probe: "원본을 읽지 못했거나 규격을 벗어났습니다.",
  transcode: "다시 시도하면 풀리는 경우가 많습니다.",
  deident: "처리가 멈춰 있습니다. 모델 구성을 확인하세요.",
  // 장애가 아닌 순서 문제다. 고칠 것 없이 지금 실행하면 되는 일이라 문구의 성격이 다르다.
  // 동작 이름은 기능 이름(`비식별화 실행`)을 쓴다.
  deident_unmasked: "비식별화를 켜기 전에 처리된 영상입니다. 영상 상세의 비식별화 실행으로 처리합니다.",
  publish: "다시 시도하면 풀리는 경우가 많습니다.",
  // 사유는 영상마다 달라 각 행이 보여 준다. 배너에 더할 내용이 없어 설명을 두지 않는다.
  analysis: "",
  // 위치 라벨이 이미 `장면 없음`이라 여기서 다시 쓰지 않는다.
  no_clips: "분석은 끝났지만 장면이 검출되지 않았습니다. 다시 분석해도 같습니다.",
  // 클립은 작업에 쓰이고 있어 진행을 멈추지 않는다는 사실과 조치를 나눠 쓴다.
  summary: "장면 요약을 받지 못했습니다. 요약 재요청으로 요약만 다시 받습니다.",
  // 버튼 이름(`요청 대기로 되돌리기`)과 같은 말로 쓴다.
  ticket: "분석 요청 정보가 없어 분석할 수 없습니다. 요청 대기로 되돌리면 다시 요청합니다.",
};

/** Job 상세의 이슈 배너를 그린다. 이슈가 0건이면 그리지 않는다. */
export function JobIssueBanner({
  issues, total, active, onPick, onClear,
}: {
  issues: Issue[];
  /** 이슈 총계. 위치별 수의 합과 같다(둘 다 서버 집계다). */
  total: number;
  /** 지금 이슈만 보고 있으면 true. 버튼 라벨이 '전체 보기'로 바뀐다(버튼은 계속 보인다, 아래 참고). */
  active?: boolean;
  onPick: () => void;
  onClear: () => void;
}) {
  if (total <= 0) return null;
  /*
    설명이 있는 위치 중 가장 큰 항목 하나의 설명은 둘째 줄에 본문 글로 두고, 나머지는 툴팁에 둔다.
    모두 펼치면 배너가 목록을 밀어내고, 툴팁에만 두면 키보드나 터치로는 볼 수 없다.
  */
  const top = [...issues]
    .filter((i) => blockedHintOr(HINT, i.stage) !== "")
    .sort((a, b) => b.count - a.count)[0];

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-bad/20 bg-bad-muted px-4 py-2">
      <TriangleAlert size={14} className="shrink-0 text-bad-text" aria-hidden="true" />
      {/* 무엇의 수인지 밝힌다. 영상 수라 단위는 `개`다. */}
      <b className="text-footnote font-semibold tabular-nums text-bad-text">
        문제 영상 {total.toLocaleString("ko-KR")}개
      </b>

      {/* 위치별 수. 배너의 핵심 정보다. 발생 위치를 보고 무엇부터 볼지 정한다. */}
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-bad-text/85">
        {issues.map((i, n) => (
          <span key={i.stage} title={blockedHintOr(HINT, i.stage) || undefined}>
            {n > 0 && <span className="mr-2 opacity-50">·</span>}
            {blockedAtLabel(i.stage)} <b className="tabular-nums">{i.count.toLocaleString("ko-KR")}</b>
          </span>
        ))}
      </span>

      {/*
        버튼을 감추지 않고 라벨만 바꾼다. 배너 높이가 고정되어 필터 전후로 아래 화면이 움직이지 않고,
        이슈 필터에서 빠져나가는 방법(전체 보기)도 생긴다.
      */}
      <button
        type="button"
        onClick={active ? onClear : onPick}
        className={cn(
          "ml-auto shrink-0 rounded-control border border-bad/30 px-2.5 py-1 text-caption font-medium",
          "text-bad-text transition-colors hover:bg-bad-muted",
        )}
      >
        {active ? "전체 보기" : "문제만 보기"}
      </button>
      {/* 위치 이름과 설명 문장을 가운뎃점으로 잇지 않는다. 가운뎃점은 나란한 속성에만 쓴다. */}
      {top && (
        <p className="w-full pl-5 text-caption text-bad-text/80">
          <span className="font-medium">{blockedAtLabel(top.stage)}</span>{" "}
          {blockedHintOr(HINT, top.stage)}
        </p>
      )}
    </div>
  );
}
