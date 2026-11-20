/**
 * 준비 상태를 나타내는 기본 표시 부품.
 *
 * 상태를 같은 크기의 원형 마크로 그려 타임라인 레일과 목록 행 어디에 놓아도 줄이 맞게 한다.
 * 채운 원(완료), 도는 고리(진행), 사선(건너뜀), 채운 원 속 느낌표(실패), 점선 원(대기),
 * 빈 원 속 느낌표(필요)다. 필요는 실패와 같은 느낌표를 쓰되 채우지 않아 실패와 구분한다.
 */
import { cn } from "@/shared/lib/cn";
import { RUN_STATUS_LABEL, RUN_STATUS_TONE, TONE_TEXT, type RunStatus } from "../lib/vocab";

/** 실행 상태를 원형 마크로 그린다. 스크린리더에는 상태 라벨을 읽게 한다. */
export function StatusMark({
  status, size = 16, className,
}: {
  status: RunStatus;
  size?: number;
  className?: string;
}) {
  const tone = RUN_STATUS_TONE[status];
  const r = size / 2;
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center", TONE_TEXT[tone], className)}
      role="img"
      aria-label={RUN_STATUS_LABEL[status]}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        {status === "ok" && (
          <>
            <circle cx={r} cy={r} r={r - 1} fill="currentColor" />
            <path
              d={`M${r * 0.6} ${r} l${r * 0.28} ${r * 0.3} l${r * 0.55} -${r * 0.62}`}
              fill="none" stroke="var(--panel)" strokeWidth={1.7}
              strokeLinecap="round" strokeLinejoin="round"
            />
          </>
        )}
        {status === "running" && (
          <>
            <circle cx={r} cy={r} r={r - 1.4} fill="none" stroke="currentColor" strokeWidth={1.6} opacity={0.28} />
            <circle
              cx={r} cy={r} r={r - 1.4} fill="none" stroke="currentColor" strokeWidth={1.9}
              strokeLinecap="round"
              strokeDasharray={`${(2 * Math.PI * (r - 1.4)) * 0.3} ${(2 * Math.PI * (r - 1.4)) * 0.7}`}
              className="origin-center motion-safe:animate-spin"
              style={{ animationDuration: "1.4s" }}
            />
          </>
        )}
        {status === "failed" && (
          <>
            <circle cx={r} cy={r} r={r - 1} fill="currentColor" />
            <path
              d={`M${r} ${r * 0.55} v${r * 0.6}`} stroke="var(--panel)"
              strokeWidth={1.8} strokeLinecap="round"
            />
            <circle cx={r} cy={r * 1.42} r={0.9} fill="var(--panel)" />
          </>
        )}
        {status === "skipped" && (
          <>
            <circle cx={r} cy={r} r={r - 1.4} fill="none" stroke="currentColor" strokeWidth={1.4} opacity={0.5} />
            <path
              d={`M${r * 0.62} ${r * 1.38} L${r * 1.38} ${r * 0.62}`}
              stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" opacity={0.7}
            />
          </>
        )}
        {status === "needed" && (
          <>
            <circle cx={r} cy={r} r={r - 1.4} fill="none" stroke="currentColor" strokeWidth={1.6} />
            <path
              d={`M${r} ${r * 0.55} v${r * 0.6}`} stroke="currentColor"
              strokeWidth={1.8} strokeLinecap="round"
            />
            <circle cx={r} cy={r * 1.42} r={0.9} fill="currentColor" />
          </>
        )}
        {status === "pending" && (
          <circle
            cx={r} cy={r} r={r - 1.4} fill="none" stroke="currentColor"
            strokeWidth={1.4} strokeDasharray="2 2.4" opacity={0.6}
          />
        )}
      </svg>
    </span>
  );
}
