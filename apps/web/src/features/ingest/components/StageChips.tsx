/**
 * Job 상세의 단계 필터 칩.
 *
 * 한 화면에 표는 하나만 두고, 상태는 필터로 거른다.
 * 상태별로 표를 쌓으면 1,000건 Job에서는 대기 표 하나가 화면 전체를 차지한다.
 *
 * 규칙:
 *  - 칩의 합은 전체 수와 같다. 집계는 서버가 센 전체 수라서 화면이 세지 않는다.
 *  - 0건 칩도 흐리게, 누를 수 없게 그린다. 숨기면 Job마다 칩 위치가 달라진다.
 *  - 칩 위에 탭을 겹쳐 두지 않는다. 칩이 탭 역할을 한다.
 */
import { cn } from "@/shared/lib/cn";
import { GROUP_LABEL, groupCount, type PrepChipKey, type PrepStage, type PrepStageCounts, type Tone } from "../lib/vocab";

/**
 * 켜진 칩의 색. 칩 고유의 톤을 그대로 쓴다.
 *
 * 선택 상태는 채움과 테두리로 알리고, 무엇을 보고 있는지는 색으로 알린다.
 * 선택을 accent(파랑) 하나로 칠하면 '이슈' 칩을 고를 때 문제 상태의 빨간색이 사라진다.
 */
const CHIP_ON: Record<Tone, string> = {
  run:  "border-accent/45 bg-accent-muted text-accent",
  wait: "border-separator-strong bg-fill text-label",
  ok:   "border-ok/45 bg-ok-muted text-ok-text",
  bad:  "border-bad/45 bg-bad-muted text-bad-text",
  mute: "border-separator-strong bg-fill text-label-2",
  act:  "border-amber-line bg-amber-soft text-amber-text",
};

/** 꺼진 칩의 색. 톤은 글자에만 남긴다. 면을 칠하면 켜진 칩과 구별되지 않는다. */
const CHIP_OFF: Record<Tone, string> = {
  run:  "border-separator text-label-3 hover:border-accent/35 hover:bg-accent-muted/50",
  wait: "border-separator text-label-3 hover:border-separator-strong hover:bg-fill",
  ok:   "border-separator text-label-3 hover:border-ok/35 hover:bg-ok-muted/50",
  bad:  "border-separator text-bad-text hover:border-bad/35 hover:bg-bad-muted/60",
  mute: "border-separator text-label-3 hover:border-separator-strong hover:bg-fill",
  act:  "border-separator text-amber-text hover:border-amber-line hover:bg-amber-soft",
};

/** 단계 필터 칩 줄을 그린다. 칩을 누르면 `onPick`으로 그 키를 넘긴다. */
export function StageChips({
  counts, active, onPick,
}: {
  counts: PrepStageCounts;
  /**
   * 지금 걸린 필터. 단계 필터(`FilterMenu`)가 넣은 서버 단계 값도 올 수 있다.
   * 그 값은 칩보다 세분돼 어느 칩과도 맞지 않으므로 활성 칩이 없다.
   */
  active: PrepChipKey | PrepStage | null;
  onPick: (key: PrepChipKey | null) => void;
}) {
  /* 칩 순서는 서버 PrepStage 순서를 따라 전체, 등록, 변환, 요청 대기, 분석 등록, 분석, 완료, 문제 순이다.
     요청 대기는 분석 서버로 보내기 전이라 분석 앞에 둔다.
     '요청 대기'는 사용자가 분석 요청을 누르지 않아 멈춘 상태다. '분석 대기'라고 부르면 이미 보낸 영상과 구분되지 않는다.
     이름은 같은 상태인 행 배지('분석 요청 대기')에 맞춘다. */
  const chips: { key: PrepChipKey | null; label: string; n: number; tone: Tone }[] = [
    { key: null, label: "전체", n: counts.total, tone: "mute" },
    { key: "register", label: "등록", n: groupCount(counts, "register"), tone: "run" },
    { key: "transcode", label: "변환", n: groupCount(counts, "transcode"), tone: "run" },
    { key: "awaiting", label: "요청 대기", n: counts.awaiting_analysis, tone: "wait" },
    { key: "mi", label: GROUP_LABEL.mi, n: groupCount(counts, "mi"), tone: "run" },
    { key: "analyze", label: GROUP_LABEL.analyze, n: groupCount(counts, "analyze"), tone: "run" },
    { key: "done", label: "완료", n: counts.ready_for_work, tone: "ok" },
    { key: "blocked", label: "문제", n: counts.blocked, tone: "bad" },
  ];

  /* 정렬 같은 다른 컨트롤과 한 줄에 놓이므로 테두리와 여백은 호출하는 쪽이 맡는다. */
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => {
        // '전체'는 다른 필터가 걸려 있지 않을 때만 켜진다. 그래서 칩은 한 번에 하나만 켜진다.
        const on = active === c.key;
        const empty = c.n === 0 && c.key !== null;
        return (
          <button
            key={c.key ?? "all"}
            type="button"
            aria-pressed={on}
            disabled={empty}
            onClick={() => onPick(c.key)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-capsule border px-2.5 py-1 text-caption transition-colors",
              on ? "font-semibold" : "font-medium",
              empty
                ? "cursor-default border-separator/50 text-label-4"
                : on
                  ? CHIP_ON[c.tone]
                  : CHIP_OFF[c.tone],
            )}
          >
            {c.label}
            {/*
              수는 칩이 켜졌을 때 배경을 둔다. 라벨과 수가 같은 무게면 수가 눈에 띄지 않는다.
              꺼진 칩에서는 글자만 진하게 한다. 배경을 칠하면 켜진 칩처럼 보인다.
            */}
            <span className={cn(
              "tabular-nums",
              empty
                ? ""
                : on
                  ? "-mr-0.5 rounded-capsule bg-panel/70 px-1.5 py-px font-bold"
                  : "font-semibold text-label-2",
            )}>
              {c.n.toLocaleString("ko-KR")}
            </span>
          </button>
        );
      })}
    </div>
  );
}
