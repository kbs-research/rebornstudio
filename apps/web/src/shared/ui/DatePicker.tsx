/**
 * 날짜 하나를 입력하는 컴포넌트. 외부 라이브러리 없이 구현한다.
 * 폼 필드용이라 앱의 Input과 같은 모양(h-10, 전체 폭)을 쓴다.
 *
 * 네이티브 `<input type="date">`는 OS마다 다르게 렌더되어 제품 톤과 어긋나므로 대신 쓴다.
 * 값은 YYYY-MM-DD 문자열이다. min/max로 고를 수 있는 범위를 제한한다(예: 생년월일 max=오늘).
 * 미래 날짜를 고르는 필드에도 쓴다.
 *
 * 날짜는 두 가지로 넣는다. 입력란에 숫자를 입력하면 `YYYY-MM-DD`로 맞춰지고, 달력 버튼를 누르면
 * 연·월을 고르는 달력이 열린다. 먼 날짜(생년월일, 수년 전 방영일)는 달력을 넘기지 않고 입력한다.
 * 이전 달·다음 달 버튼만 있으면 1990년생은 약 430번을 눌러야 한다.
 *
 * 입력란은 38px 흰 면이고 달력 버튼는 오른쪽 끝에 있다. 달력은 연·월 선택을 가운데, 이전·다음 달을 양끝에 둔다.
 * 고른 날은 먹색 채움, 일요일은 빨간 글자다. 요일 색은 하나만 쓴다. 호박색은 입력란 포커스 링에만 쓴다.
 * 바닥에 `지우기`(지울 수 있을 때)와 `오늘로`(오늘이 범위 안일 때)를 둔다.
 * 숫자만으로는 무슨 날짜인지 알 수 없으므로 날짜 버튼는 스크린리더에 `2026년 9월 21일`로 읽히고,
 * 고른 날짜에는 `aria-pressed`를 단다.
 *
 * 쓸 수 없는 날짜를 입력한 동안에는 폼 값(`value`)이 바뀌지 않는다. 그대로 제출하면 입력한 날짜가
 * 알림 없이 버려지므로, 폼은 `onProblem`으로 받은 사유가 있는 동안 제출을 막는다.
 */
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { cn } from "@/shared/lib/cn";
import { fmtDate } from "@/shared/lib/time";
import { parseYmd, toYmd } from "@/shared/lib/ymd";

/** 로컬 시간 기준 YYYY-MM-DD 문자열을 만든다. min/max에 오늘을 넘길 때 쓴다(예: `max={toYmd(new Date())}`). */
/*
  날짜와 문자열 변환의 단일 출처는 `shared/lib/ymd`다.
  이 경로로 이미 가져다 쓰는 화면(`account`)의 import를 바꾸지 않으려고
  이 파일에서 다시 내보낸다. 새 코드는 `@/shared/lib/ymd`에서 직접 가져간다.
*/
export { toYmd };
const parse = parseYmd;
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/**
 * 입력하는 대로 `YYYY-MM-DD` 꼴로 맞춘다. 숫자만 남기고 8자리까지 받아 하이픈을 넣는다.
 * 하이픈을 직접 입력해도, 숫자만 입력해도 결과가 같다.
 *
 * @example typeYmd("19900521") // "1990-05-21"
 * @example typeYmd("1990-5")   // "1990-5"
 */
export function typeYmd(raw: string): string {
  const d = raw.replace(/\D/g, "").slice(0, 8);
  if (d.length <= 4) return d;
  if (d.length <= 6) return `${d.slice(0, 4)}-${d.slice(4)}`;
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
}

/**
 * 입력한 날짜를 검사한다. 쓸 수 있으면 `null`, 아니면 화면에 보일 문장을 돌려준다.
 * 빈 문자열은 여기서 판단하지 않는다(부르는 쪽이 지우기 허용 여부로 정한다).
 */
export function checkYmd(s: string, min?: string, max?: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return "연·월·일을 모두 입력하세요. 예: 1990-05-21";
  const d = parse(s);
  if (!d || toYmd(d) !== s) return "없는 날짜입니다.";
  if (min && s < min) return `${min} 이후 날짜를 입력하세요.`;
  if (max && s > max) return `${max} 이전 날짜를 입력하세요.`;
  return null;
}

/**
 * 입력란의 글자가 폼 값에 반영되지 못한 사유를 돌려준다. 반영됐으면 `null`이다.
 *
 * 입력란과 폼 값이 다르면 입력한 날짜가 반영되지 않았다는 뜻이다.
 * 비운 입력란은 지우기로 반영되거나 초점을 떠날 때 원래 값으로 되돌아가므로 `null`을 돌려준다.
 */
export function pendingProblem(draft: string, value: string, min?: string, max?: string): string | null {
  if (draft === "" || draft === value) return null;
  return checkYmd(draft, min, max) ?? "날짜를 다시 확인하세요.";
}

/** 연도 선택지의 범위를 정한다. min/max가 있으면 그 해까지, 없으면 올해 기준 앞 100년·뒤 20년이다. */
export function yearRange(view: number, min?: string, max?: string, now = new Date()): [number, number] {
  const y = now.getFullYear();
  const lo = min ? Number(min.slice(0, 4)) : y - 100;
  const hi = max ? Number(max.slice(0, 4)) : y + 20;
  return [Math.min(lo, view), Math.max(hi, view)];
}

/** 날짜 하나를 입력하는 폼 필드를 그린다. 직접 입력과 캘린더 팝오버를 함께 쓴다. */
export function DatePicker({
  value,
  onChange,
  id,
  min,
  max,
  placeholder = "날짜 선택",
  disabled,
  clearable = true,
  className,
  onProblem,
}: {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  /** 고를 수 있는 가장 이른 날(YYYY-MM-DD). 그 전 날짜는 비활성이다. */
  min?: string;
  /** 고를 수 있는 가장 늦은 날(YYYY-MM-DD). 그 뒤 날짜는 비활성이다. 예: 생년월일은 max=오늘. */
  max?: string;
  placeholder?: string;
  disabled?: boolean;
  /** 값을 비울 수 있으면 true. 달력 바닥의 `지우기`와 입력란을 비우는 것으로 비운다. 기본값은 true. */
  clearable?: boolean;
  /** 루트 컨테이너의 클래스. 레이아웃을 조정할 때 쓴다(예: flex-1). */
  className?: string;
  /**
   * 입력란의 글자가 폼 값에 반영되지 못한 사유를 받는다. 반영되면 `null`이다.
   * 덜 입력한 날짜·없는 날짜·범위 밖 날짜가 사유가 된다. 폼은 사유가 있는 동안 제출을 막는다.
   */
  onProblem?: (problem: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const errId = useId();
  // 입력란의 글자. 끝까지 입력해 쓸 수 있는 날짜가 되었을 때만 `onChange`로 올린다.
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  // 지금 보고 있는 달(그 달 1일로 저장). value가 있으면 그 달, 없으면 이번 달로 시작한다.
  const [view, setView] = useState(() => {
    const base = parse(value) ?? new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });

  // value가 밖에서 바뀌면(폼 리셋 등) 입력란과 보이는 달도 맞춘다.
  useEffect(() => {
    setDraft(value);
    setError(null);
    const d = parse(value);
    if (d) setView(new Date(d.getFullYear(), d.getMonth(), 1));
  }, [value]);

  useOutsideClose(open, ref, () => setOpen(false));

  const pending = pendingProblem(draft, value, min, max);
  const report = useRef(onProblem);
  report.current = onProblem;
  useEffect(() => {
    report.current?.(pending);
  }, [pending]);
  // 화면에서 빠질 때 남은 사유를 지운다. 지우지 않으면 닫힌 입력란이 폼 제출을 계속 막는다.
  useEffect(() => () => report.current?.(null), []);

  // 달력 그리드. 그 달 1일의 요일 수만큼 앞을 비우고 날짜를 채운다.
  const year = view.getFullYear();
  const month = view.getMonth();
  const firstDow = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (string | null)[] = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(toYmd(new Date(year, month, d)));
  const [yearLo, yearHi] = yearRange(year, min, max);
  const years: number[] = [];
  for (let y = yearLo; y <= yearHi; y++) years.push(y);

  const outOfRange = (s: string) => (!!min && s < min) || (!!max && s > max);
  const today = toYmd(new Date());
  /** 날짜 버튼의 읽는 이름을 만든다. `2026년 9월 21일`. */
  const spoken = (s: string) => `${s.slice(0, 4)}년 ${Number(s.slice(5, 7))}월 ${Number(s.slice(8))}일`;

  const type = (raw: string) => {
    const next = typeYmd(raw);
    setDraft(next);
    if (next === "") {
      setError(null);
      if (clearable && value) onChange("");
      return;
    }
    if (next.length < 10) {
      setError(null);
      return;
    }
    const problem = checkYmd(next, min, max);
    setError(problem);
    if (!problem) {
      const d = parse(next)!;
      setView(new Date(d.getFullYear(), d.getMonth(), 1));
      if (next !== value) onChange(next);
    }
  };

  const leave = () => {
    setFocused(false);
    if (draft === "") {
      // 지울 수 없는 입력란을 비우면 입력란과 폼 값이 어긋나지 않게 원래 값으로 되돌린다.
      if (!clearable) setDraft(value);
      return;
    }
    setError(checkYmd(draft, min, max));
  };

  return (
    <div
      ref={ref}
      className={cn("relative", className)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <div
        className={cn(
          "flex h-[38px] w-full items-center gap-1.5 rounded-control border bg-panel pl-3 pr-1.5 text-callout transition-colors",
          "focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25",
          error ? "border-bad" : "border-separator-strong",
          disabled && "opacity-50",
        )}
      >
        <input
          id={id}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          maxLength={10}
          disabled={disabled}
          value={draft}
          onChange={(e) => type(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={leave}
          // 입력 중에는 형식을 보인다. 비어 있고 초점이 없을 때는 부르는 쪽이 준 이름을 보인다.
          placeholder={focused ? "YYYY-MM-DD" : placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : undefined}
          className="min-w-0 flex-1 bg-transparent tabular-nums text-label outline-none placeholder:text-placeholder disabled:cursor-not-allowed"
        />
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          aria-label="달력 열기"
          aria-haspopup="dialog"
          aria-expanded={open}
          className="grid size-7 shrink-0 place-items-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <Calendar size={14} aria-hidden="true" />
        </button>
      </div>
      {error && (
        <p id={errId} className="mt-1 text-caption text-bad-text">
          {error}
        </p>
      )}

      {open && !disabled && (
        <div
          role="dialog"
          aria-label="날짜 선택"
          className="absolute left-0 top-full z-30 mt-1.5 w-[18.5rem] origin-top animate-pop-in rounded-overlay border border-separator bg-raised p-3 shadow-pop"
        >
          {/* 월 네비게이션. 연·월은 바로 고르고, 옆 달은 버튼으로 넘긴다. */}
          <div className="mb-2 flex items-center justify-between gap-1">
            <button
              type="button"
              onClick={() => setView(new Date(year, month - 1, 1))}
              className="grid size-8 place-items-center rounded-control text-label-2 transition-colors hover:bg-fill hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              aria-label="이전 달"
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <div className="flex items-center gap-1">
              <select
                aria-label="연도"
                value={year}
                onChange={(e) => setView(new Date(Number(e.target.value), month, 1))}
                className="h-8 rounded-control border border-separator-strong bg-panel px-2 text-footnote font-semibold tabular-nums text-label focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus/25"
              >
                {years.map((y) => (
                  <option key={y} value={y}>{y}년</option>
                ))}
              </select>
              <select
                aria-label="월"
                value={month}
                onChange={(e) => setView(new Date(year, Number(e.target.value), 1))}
                className="h-8 rounded-control border border-separator-strong bg-panel px-2 text-footnote font-semibold tabular-nums text-label focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus/25"
              >
                {Array.from({ length: 12 }, (_, m) => (
                  <option key={m} value={m}>{m + 1}월</option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => setView(new Date(year, month + 1, 1))}
              className="grid size-8 place-items-center rounded-control text-label-2 transition-colors hover:bg-fill hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              aria-label="다음 달"
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>

          {/* 요일 헤더. 날짜를 읽는 기준이라 label-2로 그린다. 일요일만 빨간 글자다. */}
          <div className="grid grid-cols-7 gap-0.5 text-center text-caption text-label-2">
            {WEEKDAYS.map((w, i) => (
              <div key={w} className={cn("py-1", i === 0 && "text-bad-text")}>{w}</div>
            ))}
          </div>

          {/* 날짜 그리드 */}
          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((s, i) => {
              if (!s) return <div key={`e${i}`} />;
              const off = outOfRange(s);
              const selected = s === value;
              const isToday = s === today;
              // 첫 열이 일요일이다. 앞의 빈 날도 위치를 차지하므로 열 번호가 곧 요일이다.
              const sunday = i % 7 === 0;
              return (
                <button
                  key={s}
                  type="button"
                  disabled={off}
                  onClick={() => { onChange(s); setOpen(false); }}
                  aria-label={isToday ? `${spoken(s)}, 오늘` : spoken(s)}
                  aria-pressed={selected}
                  className={cn(
                    "h-9 rounded-control text-footnote tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                    off && "cursor-not-allowed text-label-4",
                    !off && selected && "bg-accent-fill font-semibold text-accent-fg",
                    !off && !selected && isToday && "font-semibold ring-1 ring-inset ring-separator-strong",
                    !off && !selected && (sunday ? "text-bad-text" : "text-label"),
                    !off && !selected && "hover:bg-fill",
                  )}
                >
                  {Number(s.slice(8))}
                </button>
              );
            })}
          </div>

          {/*
            바닥 줄. 지우기는 지울 수 있는 입력란에 값이 있을 때만, 오늘로는 오늘이 범위 안일 때만 둔다.
            오늘로를 누르면 먼 달을 보다가 돌아와 오늘을 고른다.
          */}
          {((clearable && value) || !outOfRange(today)) && (
            <div className="mt-2 flex items-center justify-between border-t border-separator pt-2.5">
              {clearable && value ? (
                <button
                  type="button"
                  onClick={() => { onChange(""); setOpen(false); }}
                  className="rounded-control px-2 py-1 text-footnote font-semibold text-label-2 transition-colors hover:bg-fill hover:text-label"
                >
                  지우기
                </button>
              ) : (
                <span />
              )}
              {!outOfRange(today) && (
                <button
                  type="button"
                  onClick={() => { onChange(today); setOpen(false); }}
                  aria-label={`오늘로, ${fmtDate(today)}`}
                  className="rounded-control border border-separator-strong bg-panel px-2.5 py-1 text-footnote font-semibold text-label transition-colors hover:bg-canvas-2"
                >
                  오늘로
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
