/**
 * 워크벤치에서 되돌릴 수 없는 액션(라벨링 제출 등)의 확인창.
 * 굵은 결과, 작은 근거, 취소·실행 버튼으로 이뤄진다.
 *
 * 라벨링 제출은 되돌릴 수 없다(제출 후 검수로 넘어가고 제출 취소가 없다).
 * `⌘/Ctrl+Enter`는 문장 편집 상자 안에서는 편집 확정 키다. 확인 없이 제출하면 편집을 확정하려던 사람의 작업이 그대로 제출된다.
 * 되돌릴 수 없는 액션의 판단 근거는 같은 화면에 둔다.
 *
 * 이 컴포넌트가 보장하는 규칙:
 * - 결과는 한 문장으로 굵게, 근거는 이름·값 표(`rows`)로 쓴다. 결과와 다음 할 일을 한 문장에 넣지 않는다.
 * - 제출 창은 맨 아래에 실행 뒤 열리는 곳(`next`)을 둔다.
 * - 창이 열리면 포커스는 취소 버튼에 간다(`Modal`의 `useDialogFocus`가 고르는 첫 컨트롤).
 *   그래서 습관처럼 한 번 더 누른 Enter가 실행되지 않는다.
 * - `⌘/Ctrl+Enter`는 이 창에서 실행이다. 키보드로 일하는 사람은 같은 키를 두 번 누르면 된다.
 *   창 밖(워크벤치 전역 키)으로 전달되지 않게 캡처 단계에서 받는다.
 * - 지우는 실행(`danger`)은 제목 줄에 빨간 되돌림 배지를 달아 되돌릴 수 없는 동작임을 누르기 전에 알린다.
 * - 실행은 한 번만 들어간다(`lib/confirm_gate`). 더블클릭과 `⌘/Ctrl+Enter` 연타가 요청을 둘 보내지 않는다.
 *   잠금은 실행이 끝나 `pending`이 풀리거나 창이 닫히거나 열릴 때 푼다.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { Undo2 } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Modal } from "@/shared/ui/Modal";
import { cn } from "@/shared/lib/cn";
import { createConfirmGate } from "../lib/confirm_gate";
import { Keycaps } from "./Keycaps";

/** 실행이 진행 중 상태로 바뀌지 않을 때 잠금을 푸는 시간(ms). 더블클릭 간격보다 넉넉히 길게 둔다. */
const RELEASE_FALLBACK_MS = 1000;

/** 확인 창 이름·값 표의 한 줄. `bad`는 지워지는 수처럼 되돌릴 수 없는 값에 쓴다. */
export interface ConfirmRow {
  name: ReactNode;
  value: ReactNode;
  tone?: "default" | "bad";
}

/** 비가역 액션의 확인창을 그린다. */
export function WorkbenchConfirm({
  open,
  onClose,
  onConfirm,
  title,
  result,
  facts = [],
  rows = [],
  warn,
  note,
  next,
  confirmLabel,
  pendingLabel,
  pending = false,
  tone = "primary",
  icon,
  disabled = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  /** 창 제목. 명사구로 쓴다(`작업 제출`). */
  title: string;
  /** 실행하면 무슨 일이 일어나는지 쓴 굵은 한 문장. */
  result: ReactNode;
  /** 판단 근거. 지금 이 작업의 수와 상태를 짧은 줄 여러 개로 쓴다. `rows`가 있으면 쓰지 않는다. */
  facts?: ReactNode[];
  /** 판단 근거의 이름·값 표. 나열은 셋~넷까지 둔다. */
  rows?: ConfirmRow[];
  /** 실행 전에 꼭 확인할 내용 한 줄(예: 지적 문장이 그대로 남아 있음). 호박색 줄로 따로 둔다. */
  warn?: ReactNode;
  /** 표 아래 작은 한 줄(되돌릴 수 있는지, 어떤 작업인지). */
  note?: ReactNode;
  /** 창 맨 아래 한 줄. 실행 뒤 이동할 화면(다음 작업)을 누르기 전에 보여 준다. */
  next?: ReactNode;
  /** 입력 조건이 모자라 실행할 수 없으면 true(반려 사유 없음 등). */
  disabled?: boolean;
  /** 표와 경고 사이에 들어가는 입력 등 창마다 다른 내용. */
  children?: ReactNode;
  confirmLabel: string;
  pendingLabel: string;
  pending?: boolean;
  /**
   * 실행 버튼의 색. 되살릴 수 없는 것을 지우는 실행은 `danger`,
   * 워크벤치의 제출(라벨링 제출)은 `submit`(호박색)이다. 호박색은 지금 볼 것에만 쓴다.
   */
  tone?: "primary" | "danger" | "submit";
  /** 제목 줄 아이콘. 없으면 `danger`는 되돌림 아이콘을 쓰고, 제출 창은 아이콘을 두지 않는다. */
  icon?: ReactNode;
}) {
  /* 실행 잠금. `pending`은 다음 렌더에야 버튼을 잠그므로 같은 틱의 두 번째 클릭을 이 잠금으로 막는다. */
  const gate = useRef(createConfirmGate()).current;
  /* 창이 닫히거나 다시 열리면 잠금을 푼다. 실행이 끝나 `pending`이 풀려도 푼다. 실패한 실행은 창에서 다시 누를 수 있다. */
  const wasPending = useRef(pending);
  useEffect(() => {
    if (wasPending.current && !pending) gate.release();
    wasPending.current = pending;
  }, [pending, gate]);
  useEffect(() => {
    gate.release();
  }, [open, gate]);
  /* 실행이 `pending`을 한 번도 그리지 않고 끝나면(아주 빠른 실패, 아무것도 하지 않은 `onConfirm`) 위의 해제가 실행되지 않는다.
     그래서 잠시 뒤에도 진행 중이 아니면 푼다. 그때까지 들어온 클릭은 막는다. */
  const pendingNow = useRef(pending);
  pendingNow.current = pending;
  const fallback = useRef<number>();
  useEffect(() => () => window.clearTimeout(fallback.current), []);
  /** 실행한다. 진행 중이거나 입력이 모자라거나 이미 시작된 실행이 있으면 아무것도 하지 않는다. */
  const run = () => {
    if (pending || disabled || !gate.tryEnter()) return;
    window.clearTimeout(fallback.current);
    fallback.current = window.setTimeout(() => {
      if (!pendingNow.current) gate.release();
    }, RELEASE_FALLBACK_MS);
    onConfirm();
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key !== "Enter") return;
      e.preventDefault();
      e.stopPropagation();
      run();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  /* 액션 줄은 `Modal`의 `footer`가 그린다. 근거가 길어 본문이 스크롤돼도 버튼이 함께
     밀려나지 않는다. pending 문구와 disabled는 이 컴포넌트가 정한다.
     그 조건은 창마다 달라서 `Modal`로 옮기면 두 곳에서 정하게 된다. */
  const footer = (
    <>
      <Button variant="secondary" size="sm" onClick={onClose}>취소</Button>
      <Button variant={tone} size="sm" disabled={pending || disabled} onClick={run}>
        {pending ? pendingLabel : confirmLabel}
        {tone === "submit" && !pending && !disabled && <Keycaps keys={["⌘", "↵"]} tone="on-amber" />}
      </Button>
    </>
  );
  const badge = icon ?? (tone === "danger" ? <Undo2 size={16} aria-hidden="true" /> : undefined);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={footer}
      icon={badge}
      tone={badge ? (tone === "danger" ? "danger" : tone === "submit" ? "warn" : "accent") : undefined}
    >
      {/*
        결과 문장은 본문이다. 나가기 창(`features/me/pages/inbox.tsx`)과 같은 굵기로 두어
        절 제목처럼 보이지 않게 한다.
      */}
      <p className="text-callout font-semibold text-label">{result}</p>
      {rows.length > 0 ? (
        <dl className="mt-3 space-y-2 rounded-card border border-separator bg-canvas-2 px-4 py-3 text-footnote">
          {rows.map((r, i) => (
            <div key={i} className="flex items-baseline justify-between gap-4">
              <dt className="shrink-0 text-label-3">{r.name}</dt>
              <dd className={cn("text-right font-medium tabular-nums", r.tone === "bad" ? "text-bad-text" : "text-label")}>
                {r.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : facts.length > 0 ? (
        <ul className="mt-2 space-y-1 text-footnote text-label-2">
          {facts.map((f, i) => (
            <li key={i} className="flex gap-1.5">
              <span aria-hidden="true" className="text-label-3">·</span>
              <span>{f}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {children}
      {warn && (
        <p className="mt-3 flex items-center gap-2 rounded-card border border-amber-line bg-amber-soft px-3 py-2 text-footnote font-medium text-label">
          <span className="size-2 shrink-0 rounded-capsule bg-amber" aria-hidden="true" />
          {warn}
        </p>
      )}
      {note && <p className="mt-3 text-footnote text-label-3">{note}</p>}
      {next && <div className="mt-3 border-t border-separator pt-3 text-footnote text-label-2">{next}</div>}
    </Modal>
  );
}
