/**
 * 연동 진단 결과 목록. 프로젝트 설정의 저장소·분석 연결 카드와 플랫폼 개요(`/admin`)가 함께 쓴다.
 *
 * 두 화면이 같은 응답을 각자 그리면 판정 색과 문구가 달라진다(판정 규칙은 한 곳에만).
 * 타입도 계약 타입을 그대로 쓴다.
 *
 * 이 컴포넌트는 그리기만 한다. 언제 진단을 부를지는 화면이 정한다.
 * 진단은 외부 서비스를 실제로 호출하므로 자동으로 실행하지 않는다. 플랫폼 개요는 서버의 주기 점검 결과를 읽고,
 * 프로젝트 설정은 저장소·분석 대상 점검 버튼을 누를 때만 실행한다.
 */
import { cn } from "@/shared/lib/cn";
import type { DiagnosticsReport } from "@/shared/api/client";

import { analysisOffText } from "../lib/analysis_off";

/**
 * 진단 상태별 표시. ok는 확인됐다는 뜻이고, warn은 지금은 동작하지만 운영에서는 문제가 된다는 뜻이다.
 * fail은 그대로 두면 기능이 동작하지 않는다는 뜻이다.
 * unchecked는 점검 기록이 없어 확인 전이라는 뜻이다. 이상 상태와 구분해 흐린 점으로 그린다.
 * 판정은 점과 글자로 함께 표시하고 색에만 의존하지 않는다(WCAG 1.4.1).
 */
const TONE = {
  fail: { dot: "bg-bad", word: "실패", rank: 0 },
  warn: { dot: "bg-warn", word: "주의", rank: 1 },
  unchecked: { dot: "bg-label-4", word: "확인 전", rank: 2 },
  ok: { dot: "bg-ok", word: "정상", rank: 3 },
} as const;

type Tone = keyof typeof TONE;
/** 진단 상태를 표시 톤으로 바꾼다. 표에 없는 상태는 주의로 둔다. */
const toneOf = (status: string): Tone => ((status as Tone) in TONE ? (status as Tone) : "warn");

/**
 * 진단 요약 문구를 만든다. 분모(전체 건수)와 정상 수를 먼저 쓰고, 정상이 아닌 것을 종류별로 붙인다.
 *
 * 실패가 0건이어도 warn이 남아 있으면 그 수도 쓴다.
 * 확인 전(`unchecked`)은 주의로 세지 않고 따로 센다. 확인 전 항목이 있으면 모두 정상이라고 쓰지 않는다.
 */
export function diagnosticsSummary(report: DiagnosticsReport): string {
  const count = (status: string) => report.checks.filter((c) => c.status === status).length;
  const total = report.checks.length;
  const ok = count("ok");
  if (ok === total) return `${total}건 모두 정상`;
  const rest = [
    report.failed > 0 && `실패 ${report.failed}`,
    count("warn") > 0 && `주의 ${count("warn")}`,
    count("unchecked") > 0 && `확인 전 ${count("unchecked")}`,
  ].filter(Boolean) as string[];
  return [`${total}건 중 ${ok}건 정상`, ...rest].join(" · ");
}

/**
 * 진단 항목 목록을 그린다. 실패·주의를 먼저 두고, 같은 판정 안에서는 서버 순서를 지킨다.
 * 한 줄은 판정, 이름, 측정값과 안내의 세 열이다. 줄 사이는 선으로 나눈다.
 */
export function DiagnosticsList({ report, className }: { report: DiagnosticsReport; className?: string }) {
  // 분석 연동이 꺼진 상태는 점검마다 문장이 달라 한 문구로 맞춘다(`lib/analysis_off`).
  const rows = report.checks
    .map(analysisOffText)
    .sort((a, b) => TONE[toneOf(a.status)].rank - TONE[toneOf(b.status)].rank);
  return (
    <ul className={cn("divide-y divide-separator", className)}>
      {rows.map((c) => {
        const t = TONE[toneOf(c.status)];
        return (
          <li key={c.key} className="grid grid-cols-[64px_minmax(0,140px)_minmax(0,1fr)] items-baseline gap-x-4 px-5 py-3">
            <span className="inline-flex items-center gap-1.5 text-footnote font-semibold text-label">
              <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-capsule", t.dot)} />
              {t.word}
            </span>
            <span className="text-footnote font-semibold text-label">{c.label}</span>
            <span className="min-w-0">
              {/* detail은 무엇을 확인했는지 알리는 측정값이다. 이 값이 없으면 판정을 믿을 근거가 없다. */}
              <span className="block text-footnote text-label">{c.detail}</span>
              {c.hint && <span className="mt-0.5 block text-caption text-label-3">{c.hint}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
