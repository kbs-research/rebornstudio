import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronLeft } from "lucide-react";

import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";
import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { cn } from "@/shared/lib/cn";
import { Keycaps } from "./Keycaps";

/**
 * 워크벤치(라벨링·검수·완료 보기) 공통 머리 줄.
 *
 * 56px 한 줄이다. 순서는 뒤로 가기, 제목 두 줄, 수 묶음(반려 횟수 등),
 * 저장 상태, 도구, 임시저장, 제출이다. 워크벤치별 차이는 슬롯과 prop으로 넣는다.
 * 1440px 폭에서 한 줄에 들어오도록 도구는 아이콘으로 접고(`compact`), 관련 도구는 메뉴 하나로 묶을 수 있다(`menu`).
 * 모든 글자는 nowrap이고 제목만 줄임표로 자른다. 뒤로 가기와 주 버튼은 늘 보인다.
 */
export interface WbAction {
  key: string;
  icon: ReactNode;
  label: string;
  /** to가 있으면 Link로, 없으면 button(onClick)으로 그린다. */
  to?: string;
  onClick?: () => void;
  /** 눌린 상태면 true(서랍이 열린 도구). `aria-pressed`로도 알린다. */
  active?: boolean;
  /**
   * 눌러도 갈 곳이 없으면 true. 목록의 처음이나 끝에서 이전/다음 버튼 같은 경우다.
   * 버튼을 숨기지 않고 비활성으로 둔다. 버튼이 사라졌다 나타나면 머리 줄이 흔들린다.
   * `to`가 있는 액션에는 쓰지 않는다(링크에는 늘 이동할 곳이 있다).
   */
  disabled?: boolean;
  /**
   * 아이콘만으로 뜻이 전달되는 도구(단축키 등)면 true. 이름은 `title`·`aria-label`로 남는다.
   * 좁은 폭에서 도구 이름까지 모두 보이면 머리 줄이 한 줄을 넘는다.
   */
  compact?: boolean;
  /** 누를 때 여는 메뉴의 항목. 관련 도구 여럿을 버튼 하나로 묶는다. */
  menu?: { key: string; icon?: ReactNode; label: string; onSelect: () => void }[];
  /** 앞에 세로 구분선을 두면 true. 성격이 다른 도구 무리를 가른다. */
  separatorBefore?: boolean;
}

/** 머리 줄 가운데 묶음의 한 항목(반려 2회, 클립 3 / 40 등). */
export interface WbStat {
  key: string;
  label: string;
  value: ReactNode;
  /** 값의 색조. `bad`는 지금 막힌 값에 쓴다. */
  tone?: "default" | "bad";
  /** 항목 전체의 보조 설명. 화면 낭독기와 마우스 호버가 읽는다. */
  title?: string;
}

interface WbButton {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** 버튼 안 키캡(예: `⌘ ↵`). 화면 낭독기에는 읽히지 않는다. */
  keys?: string[];
}

interface Props {
  backTo?: string;
  backLabel?: string;
  /** 워크벤치 종류(라벨링·검수·완료 클립). 탭 제목과 문서 개요의 이름에 쓴다. */
  badge?: string;
  /** mono 식별자(work_id 8자리 등). `subtitle`이 없을 때 둘째 줄 끝에 붙는다. */
  idText?: string;
  /** 영상 제목 같은 주 식별자(줄임표로 자른다). */
  title?: string;
  /** 제목 아래 mono 한 줄(구간·길이·식별자). 없으면 `range`와 `idText`를 잇는다. */
  subtitle?: ReactNode;
  /** 구간 문자열. `subtitle`이 없을 때 둘째 줄에 쓴다. */
  range?: ReactNode;
  /** 가운데 묶음의 항목(반려 횟수·클립 순번). */
  stats?: WbStat[];
  /** 가운데 큐 정보. `stats`가 없을 때 묶음 위치에 그린다. */
  queue?: ReactNode;
  /** 남은 시간 슬롯. `stats`가 없을 때 쓴다. */
  timer?: ReactNode;
  /** 상태 슬롯(저장 상태, 제출됨 등). */
  status?: ReactNode;
  /** 도구(가이드·신고·단축키). */
  actions?: WbAction[];
  /** 보조 버튼(임시저장). */
  secondary?: WbButton;
  /** 주 버튼. `tone`이 submit이면 호박색이다(워크벤치 제출). 호박색은 지금 볼 것에만 쓴다. */
  primary?: WbButton & { tone?: "submit" | "neutral" };
  /**
   * 브라우저 탭 제목. 없으면 `제목 · 배지`를 쓴다.
   * 제목 위치에 수(`전체 432개`)를 쓰는 화면은 화면 이름을 따로 준다.
   */
  docTitle?: string;
}

const BACK =
  "inline-flex h-8 shrink-0 items-center gap-1 rounded-control pl-1 pr-2 text-footnote text-label-2 transition-colors hover:bg-fill hover:text-label";
const TOOL =
  "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-control px-2 text-footnote font-medium transition-colors hover:bg-fill hover:text-label disabled:pointer-events-none disabled:text-label-4";
const SECONDARY =
  "inline-flex h-8 shrink-0 items-center rounded-control border border-separator-strong bg-panel px-3 text-footnote font-semibold text-label transition-colors hover:bg-raised disabled:cursor-not-allowed disabled:text-label-4";
const PRIMARY_TONE = {
  submit: "bg-amber text-on-amber hover:bg-amber-strong",
  neutral: "bg-accent-fill text-accent-fg hover:bg-accent-fill-hover",
} as const;
/*
  비활성 주 버튼도 버튼 모양을 남긴다. 머리 줄 바탕과 같은 면(`bg-canvas-2`)으로 칠하면 글자만 떠서
  누를 수 없는 버튼 대신 설명 글처럼 읽힌다.
*/
const PRIMARY =
  "inline-flex h-8 shrink-0 items-center gap-2 rounded-control px-4 text-footnote font-bold transition-colors " +
  "disabled:cursor-not-allowed disabled:border disabled:border-separator-strong disabled:bg-fill disabled:text-label-3";

/** 누르면 항목 메뉴가 열리는 도구 버튼을 그린다. */
function ToolMenu({ action }: { action: WbAction }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClose(open, ref, () => setOpen(false));
  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={action.disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(TOOL, open ? "bg-fill text-label" : "text-label-2")}
      >
        {action.icon}
        <span className={action.compact ? "hidden 2xl:inline" : "hidden lg:inline"}>{action.label}</span>
        <ChevronDown size={14} aria-hidden="true" className="text-label-3" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label={action.label}
          className="absolute right-0 top-full z-30 mt-1.5 w-52 animate-pop-in overflow-hidden rounded-card border border-separator bg-raised p-1 shadow-pop"
        >
          {action.menu!.map((m) => (
            <button
              key={m.key}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                m.onSelect();
              }}
              className="flex w-full items-center gap-2 rounded-control px-2.5 py-2 text-left text-footnote text-label transition-colors hover:bg-fill"
            >
              {m.icon && <span className="shrink-0 text-label-3">{m.icon}</span>}
              {m.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 워크벤치 머리 줄을 그린다. 열려 있는 동안 셸 영역을 `inert`로 만든다. */
export function WorkbenchHeader({
  backTo = "/my/work",
  backLabel = "내 작업",
  badge,
  idText,
  title,
  subtitle,
  range,
  stats,
  queue,
  timer,
  status,
  actions,
  secondary,
  primary,
  docTitle,
}: Props) {
  /* 전체 화면 워크벤치에는 `PageHeader`가 없어 탭 제목을 여기서 올린다.
     올리지 않으면 워크벤치의 탭 제목이 앱 이름이나 뒤에 깔린 화면 이름이 된다. */
  useDocumentTitle(docTitle ?? ([title, badge].filter(Boolean).join(" · ") || null));

  /**
   * 워크벤치가 열린 동안 셸 영역을 `inert`로 만든다.
   *
   * 워크벤치는 모두 `fixed inset-0 z-workbench`로 화면 전체를 덮지만 뒤에 깔린 사이드바와 상단바가
   * 탭 순서에서 먼저 온다. 포커스를 한 번 옮기는 방법은 포커스가 다시 빠지면 되돌릴 수 없다.
   * `inert`는 열려 있는 동안 계속 유지되므로 `inert`를 쓴다.
   * 표식은 `_layout`의 셸 요소 둘(`AppSidebar`·`TopBar`)이 `data-shell-chrome`으로 단다.
   */
  useEffect(() => {
    const chrome = [...document.querySelectorAll<HTMLElement>("[data-shell-chrome]")];
    chrome.forEach((el) => (el.inert = true));
    return () => chrome.forEach((el) => (el.inert = false));
  }, []);

  const second = subtitle ?? ([range, idText].some(Boolean) ? (
    <>
      {range}
      {range && idText ? " · " : null}
      {idText}
    </>
  ) : null);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-separator bg-canvas-2 pl-3 pr-4">
      {/*
        화면 이름은 문서 개요에만 준다(sr-only `h1`). 보이는 제목은 영상 제목이다.
        이름은 `{단계} 워크벤치`로 쓴다. 워크벤치마다 `h1`이 하나 있어야 한다.
      */}
      <h1 className="sr-only">{[badge ? `${badge} 워크벤치` : "워크벤치", title].filter(Boolean).join(", ")}</h1>

      <Link to={backTo} className={BACK}>
        <ChevronLeft size={16} aria-hidden="true" />
        <span className="hidden sm:inline">{backLabel}</span>
        <span className="sr-only sm:hidden">{backLabel}</span>
      </Link>
      <span className="h-6 w-px shrink-0 bg-separator" aria-hidden="true" />

      <div className="flex min-w-0 flex-col gap-0.5">
        {title && <span className="truncate text-callout font-semibold leading-tight text-label">{title}</span>}
        {second && (
          <span className="truncate font-mono text-micro leading-tight text-label-3">{second}</span>
        )}
      </div>

      <div className="min-w-2 flex-1" />

      {stats && stats.length > 0 ? (
        <span className="hidden h-8 shrink-0 items-center rounded-control border border-separator bg-panel md:flex">
          {stats.map((s, i) => (
            <span key={s.key} className="flex items-center" title={s.title}>
              {i > 0 && <span className="h-[18px] w-px bg-separator" aria-hidden="true" />}
              <span className="flex items-baseline gap-1.5 px-2.5">
                <span className="text-micro text-label-3">{s.label}</span>
                <span
                  className={cn(
                    "font-mono text-footnote font-semibold tabular-nums",
                    s.tone === "bad" ? "text-bad-text" : "text-label",
                  )}
                >
                  {s.value}
                </span>
              </span>
            </span>
          ))}
        </span>
      ) : (
        <>
          {queue && (
            <span className="hidden shrink-0 items-center whitespace-nowrap text-micro text-label-3 lg:flex">{queue}</span>
          )}
          {timer && <span className="hidden shrink-0 items-center whitespace-nowrap md:flex">{timer}</span>}
        </>
      )}

      {status && <span className="hidden shrink-0 items-center gap-2 whitespace-nowrap lg:flex">{status}</span>}

      {actions && actions.length > 0 && (
        <div className="flex shrink-0 items-center gap-0.5">
          {actions.map((a) => {
            const sep = a.separatorBefore ? (
              <span key={a.key + "-sep"} className="mx-1.5 h-5 w-px shrink-0 bg-separator" aria-hidden="true" />
            ) : null;
            if (a.menu && a.menu.length > 0) {
              return [sep, <ToolMenu key={a.key} action={a} />];
            }
            const cls = cn(TOOL, a.active ? "bg-fill text-label" : "text-label-2");
            const body = (
              <>
                {a.icon}
                {!a.compact && <span className="hidden lg:inline">{a.label}</span>}
                {a.compact && <span className="hidden 2xl:inline">{a.label}</span>}
              </>
            );
            return [
              sep,
              a.to ? (
                <Link key={a.key} to={a.to} className={cls} title={a.label} aria-label={a.label}>
                  {body}
                </Link>
              ) : (
                <button
                  key={a.key}
                  type="button"
                  onClick={a.onClick}
                  disabled={a.disabled}
                  aria-pressed={a.active ?? undefined}
                  className={cls}
                  title={a.label}
                  aria-label={a.label}
                >
                  {body}
                </button>
              ),
            ];
          })}
        </div>
      )}
      {secondary && (
        <button type="button" onClick={secondary.onClick} disabled={secondary.disabled} className={SECONDARY}>
          {secondary.label}
        </button>
      )}
      {primary && (
        <button
          type="button"
          onClick={primary.onClick}
          disabled={primary.disabled}
          className={cn(PRIMARY, PRIMARY_TONE[primary.tone ?? "neutral"])}
        >
          {primary.label}
          {primary.keys && !primary.disabled && <Keycaps keys={primary.keys} tone="on-amber" />}
        </button>
      )}
    </header>
  );
}
