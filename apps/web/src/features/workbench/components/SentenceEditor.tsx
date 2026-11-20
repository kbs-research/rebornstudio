import {
  Fragment,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";

import { type CaptionSentence, newSentenceId } from "../lib/caption";
import { EDIT_SHORTCUTS, hit } from "../lib/shortcuts";

import { splitSentences } from "./CaptionView";
import { WB_CHIP_TIGHT } from "./chip";

/**
 * 라벨링 캡션을 문장 카드 단위로 작성한다. 초안을 문장별로 수정·삭제하고 끝에 문장을 더한다.
 *
 * 카드와 삭제 방식은 검수 워크벤치의 `SentenceTriage`와 같다. 삭제한 문장은 배열에서 빼지 않고
 * 삭제됨 표시(취소선과 되돌리기)로 남겨 언제든 복원할 수 있다. 전역 토스트의 실행 취소는 쓰지 않는다.
 * `onChange`에는 삭제 표시를 포함한 문장 배열을 보낸다. 상위가 `joinSentences`로 최종 캡션을 만들 때 삭제한 문장이 빠진다.
 *
 * 새 문장은 맨 끝의 작성 슬롯에서 쓰고 확인할 때만 배열에 더한다.
 * 그래서 작성을 중단해도 빈 문장 카드가 남지 않는다.
 */

/**
 * 문장 항목. 삭제 표시한 문장은 최종 캡션에서 빠지지만 편집 화면에는 취소선으로 남는다.
 *
 * `lib/caption`의 `CaptionSentence`를 그대로 쓴다. 문장 식별자가 있어야 검수 판정을 그 문장에 붙일 수 있다.
 */
type Sent = CaptionSentence;

/**
 * 조작 도구를 지금 다루는 행에서만 보이게 하는 클래스.
 *
 * 모든 행에 수정·삭제 아이콘이 늘 보이면 읽어야 할 문장보다 아이콘이 눈에 띈다.
 * 포커스로도 열리므로 키보드 사용자에게도 보인다.
 * 보이지 않는 동안에는 클릭도 막아 보이지 않는 삭제 버튼이 눌리지 않게 한다.
 */
const REVEAL_ON_HOVER =
  "opacity-0 transition-opacity pointer-events-none " +
  "group-hover:pointer-events-auto group-hover:opacity-100 " +
  "group-focus-within:pointer-events-auto group-focus-within:opacity-100";

/** 문장 카드 편집기를 그린다. */
export function SentenceEditor({
  value,
  onChange,
  fontSize,
  flaggedTexts = [],
  baseline = "",
  readOnly = false,
}: {
  /** 문장 배열(원본). 문자열은 상위가 `joinSentences`로 만든다. */
  value: CaptionSentence[];
  onChange: (next: CaptionSentence[]) => void;
  fontSize: number;
  /** 검수에서 지적(반려)된 문장 텍스트(prefix). 앞부분이 일치하는 문장을 호박색 지적 카드로 표시한다. */
  flaggedTexts?: string[];
  /**
   * AI 초안(원본). 이 목록에 없는 문장은 라벨러가 고치거나 새로 쓴 것이라
   * `교정됨`으로 표시한다.
   */
  baseline?: string;
  /**
   * 편집할 수 없는 상태(보유하지 않음)면 true. 수정·삭제·추가 버튼을 그리지 않는다.
   */
  readOnly?: boolean;
}) {
  const [sentences, setSentences] = useState<Sent[]>(value);
  const [editIdx, setEditIdx] = useState<number | null>(null); // 기존 문장을 수정 중인 위치
  // 맨 끝 작성 슬롯이 열려 있으면 true. 빈 캡션으로 진입하면 슬롯을 자동으로 열어 바로 입력하게 한다.
  const [composing, setComposing] = useState(() => value.length === 0);
  const [draft, setDraft] = useState("");
  // 이 컴포넌트가 마지막으로 내보낸 배열. 같은 배열이 `value`로 돌아오면 외부 동기화를 건너뛴다.
  const lastEmit = useRef<Sent[]>(value);
  /**
   * 편집을 닫은 뒤 포커스를 돌려놓을 문장 위치.
   *
   * 돌려놓지 않으면 포커스가 `<body>`로 떨어진다. 문장이 여럿인 클립에서 둘째 문장을 고치려면
   * 사이드바부터 Tab을 다시 시작해야 한다.
   */
  const refocus = useRef<number | null>(null);
  // 텍스트 앞부분이 지적 문자열과 일치하면 지적 문장이다.
  // 라벨러가 문장을 고치면 일치하지 않게 되어 표시가 풀린다.
  const isFlagged = (t: string) => flaggedTexts.some((f) => f && t.trim().startsWith(f));
  /*
    한 번이라도 지적과 맞았던 문장의 id. 글을 고치면 지적 문자열과 더는 맞지 않는다.
    그 문장을 `고침`으로 표시하려면 원래 지적이었다는 사실을 문장 id로 기억해야 한다.
    지적 문자열은 반려 때의 문장 앞부분이라 id가 함께 오지 않는다.
  */
  const everFlagged = useRef(new Set<string>());
  // AI 초안 문장 집합. 이 집합에 없는 문장은 라벨러가 고쳤거나 새로 썼다고 보고 `교정됨`으로 표시한다.
  // 초안이 없으면 비교하지 않는다.
  const baseSet = useMemo(
    () => new Set(splitSentences(baseline).map((t) => t.trim()).filter(Boolean)),
    [baseline],
  );
  const isModified = (t: string) => baseSet.size > 0 && !!t.trim() && !baseSet.has(t.trim());

  // 외부에서 value가 바뀌면(초안 적용, 작업 전환) 다시 동기화하고 진행 중인 편집·작성을 초기화한다.
  // 새 작업이 빈 캡션이면 작성 슬롯을 자동으로 열어 라벨러가 바로 입력하게 한다.
  // 이 컴포넌트가 방금 올려 보낸 배열이 돌아온 경우는 참조 비교로 걸러 건드리지 않는다.
  // 원본은 배열이라 합친 문자열로는 비교할 수 없다.
  useEffect(() => {
    if (value !== lastEmit.current) {
      setSentences(value);
      lastEmit.current = value;
      setEditIdx(null);
      setComposing(value.length === 0 && !readOnly);
    }
    // readOnly는 의존성에서 일부러 뺀다. 값이 바뀔 때만 다시 맞춘다.
    // 잠김 전환은 아래 이펙트가 처리한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // 잠기면 열려 있던 작성·편집 상자를 닫는다. 잠긴 상자가 떠 있으면 쓸 수 있는 것처럼 보인다.
  useEffect(() => {
    if (readOnly) {
      setComposing(false);
      setEditIdx(null);
    }
  }, [readOnly]);

  // 빈(공백) 문장은 상태 어디에도 남기지 않는다. 삭제 표시한 문장은 되돌릴 수 있게 남긴다.
  // 최종 문자열은 상위가 `joinSentences`로 만든다. 파생값을 만드는 곳을 하나로 둔다.
  const commit = (next: Sent[]) => {
    const cleaned = next.filter((s) => s.deleted || s.text.trim() !== "");
    setSentences(cleaned);
    lastEmit.current = cleaned;
    onChange(cleaned);
  };

  // 새 문장 작성이다. 배열에 끼우지 않고 작성 슬롯만 연다(중단해도 빈 문장이 생기지 않는다).
  const openCompose = () => {
    setEditIdx(null);
    setDraft("");
    setComposing(true);
  };
  const confirmCompose = () => {
    if (!composing) return;
    const t = draft.trim();
    if (t) commit([...sentences, { id: newSentenceId(), text: t }]);
    setComposing(false);
  };
  const cancelCompose = () => setComposing(false);

  // 기존 문장을 수정한다.
  const startEdit = (i: number) => {
    setComposing(false);
    setDraft(sentences[i].text);
    setEditIdx(i);
  };
  const confirmEdit = () => {
    if (editIdx === null) return;
    refocus.current = editIdx;
    const t = draft.trim();
    // 비우고 확인하면 문장을 제거한다(빈 카드 방지).
    commit(t ? sentences.map((s, j) => (j === editIdx ? { ...s, text: t } : s)) : sentences.filter((_, j) => j !== editIdx));
    setEditIdx(null);
  };
  const cancelEdit = () => {
    refocus.current = editIdx;
    setEditIdx(null);
  };

  /**
   * 편집 상자 안의 키 처리기를 만든다. 편집 상자는 키보드만으로 열고 닫을 수 있다.
   *
   * `Esc`는 취소, `⌘/Ctrl+Enter`는 확정이다. 키 표는 `lib/shortcuts.ts`의 `EDIT_SHORTCUTS`다.
   * 위쪽 워크벤치·모달에도 같은 키의 처리기가 있어서 이벤트 전파를 막는다.
   * 막지 않으면 문장 하나를 확정하면서 작업 전체가 제출되거나 상위 창이 함께 닫힌다.
   */
  const editorKeys = (confirm: () => void, cancel: () => void) => (e: ReactKeyboardEvent) => {
    if (hit(EDIT_SHORTCUTS, "cancel", e.nativeEvent)) {
      e.preventDefault();
      e.stopPropagation();
      cancel();
      return;
    }
    if (hit(EDIT_SHORTCUTS, "confirm", e.nativeEvent)) {
      e.preventDefault();
      e.stopPropagation();
      confirm();
    }
  };

  // 편집이 닫히면 그 문장의 `문장 수정` 버튼으로 포커스를 돌려놓는다(위 `refocus` 참조).
  // 문장이 지워졌으면(비우고 확인) 돌아갈 버튼이 없으므로 위치만 비운다.
  useEffect(() => {
    const i = refocus.current;
    if (i === null || editIdx !== null) return;
    refocus.current = null;
    const btn = document.querySelector<HTMLElement>(`[data-edit-btn="${i}"]`);
    btn?.focus();
  }, [editIdx, sentences]);

  // 삭제는 배열에 남기고 삭제 표시만 한다(검수와 같다). 되돌리기는 카드 안 버튼으로 한다.
  const markDeleted = (i: number) => commit(sentences.map((s, j) => (j === i ? { ...s, deleted: true } : s)));
  const restore = (i: number) => commit(sentences.map((s, j) => (j === i ? { ...s, deleted: false } : s)));

  /** 새 문장 작성 상자를 그린다. 맨 끝 작성 슬롯(배열 밖)에서만 쓴다. */
  const composeBox = (num: number) => (
    <div className="rounded-card border border-accent bg-panel px-2.5 py-2 shadow-xs">
      <div className="mb-1.5 flex items-center gap-1.5 text-micro font-medium text-accent">
        <Pencil size={12} /> 문장 {num} 작성
      </div>
      <textarea
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={editorKeys(confirmCompose, cancelCompose)}
        rows={3}
        placeholder="문장을 입력하세요"
        aria-label="문장 작성"
        className="w-full resize-y min-h-[4.5rem] rounded-field border border-separator-strong bg-panel px-3 py-2 leading-relaxed text-label placeholder:text-placeholder transition-colors focus:border-label-3 focus:outline-none"
        style={{ fontSize: `${fontSize}px`, lineHeight: 1.5 }}
      />
      <div className="mt-2 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={cancelCompose}
          className="rounded-control px-2.5 py-1 text-micro font-medium text-label-2 transition-colors hover:bg-fill focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          취소
        </button>
        <button
          type="button"
          onClick={confirmCompose}
          className="rounded-control bg-accent-fill px-3 py-1 text-micro font-semibold text-accent-fg transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-1"
        >
          확인
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-2">
      {sentences.map((s, i) => {
        if (editIdx === i) {
          return (
            <Fragment key={i}>
              <div className="rounded-card border border-accent bg-panel px-2.5 py-2 shadow-xs">
                <div className="mb-1.5 flex items-center gap-1.5 text-micro font-medium text-accent">
                  <Pencil size={12} /> 문장 {i + 1} 수정
                </div>
                <textarea
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={editorKeys(confirmEdit, cancelEdit)}
                  rows={2}
                  placeholder="문장을 입력하세요"
                  /* 보이는 머리글(`문장 N 수정`)과 같은 이름을 쓴다. 눈으로 읽는 이름과
                     화면 낭독기가 읽는 이름이 다르면 같은 요소를 두 이름으로 부르게 된다. */
                  aria-label={`문장 ${i + 1} 수정`}
                  className="w-full resize-none rounded-field border border-separator-strong bg-panel px-2.5 py-1.5 leading-relaxed text-label placeholder:text-placeholder transition-colors focus:border-label-3 focus:outline-none"
                  style={{ fontSize: `${fontSize}px`, lineHeight: 1.5 }}
                />
                <div className="mt-2 flex justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={cancelEdit}
                    className="rounded-control px-2.5 py-1 text-micro font-medium text-label-2 transition-colors hover:bg-fill focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    onClick={confirmEdit}
                    className="rounded-control bg-accent-fill px-3 py-1 text-micro font-semibold text-accent-fg transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-1"
                  >
                    확인
                  </button>
                </div>
              </div>
            </Fragment>
          );
        }

        // 삭제 표시 카드. 검수 `SentenceTriage`의 `del`과 같은 방식(취소선, 삭제됨, 되돌리기)이다.
        if (s.deleted) {
          return (
            <Fragment key={i}>
              <div className="flex items-center gap-2 rounded-card border border-separator bg-fill-2 px-2.5 py-2">
                <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-capsule bg-fill-3 text-micro font-medium text-label-4">
                  {i + 1}
                </span>
                <span className="flex-1 text-label-4 line-through" style={{ fontSize: `${fontSize}px`, lineHeight: 1.55 }}>
                  {s.text}
                </span>
                <span className="shrink-0 rounded-control bg-fill-3 px-1.5 py-0.5 text-nano font-medium text-label-3">삭제됨</span>
                <button
                  type="button"
                  onClick={() => restore(i)}
                  title="되돌리기"
                  aria-label={`문장 ${i + 1} 삭제 되돌리기`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-label-4 transition-colors hover:bg-fill hover:text-label-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                >
                  <RotateCcw size={14} />
                </button>
              </div>
            </Fragment>
          );
        }

        const flagged = isFlagged(s.text);
        if (flagged) everFlagged.current.add(s.id);
        const fixed = !flagged && everFlagged.current.has(s.id);
        // 지적이 교정 표시보다 우선한다. 아직 고칠 대상이면 지적으로 표시한다.
        const modified = !flagged && isModified(s.text);
        return (
          <Fragment key={i}>
            <div
              className={
                "group flex items-start gap-1.5 rounded-card border px-3 py-2.5 transition-colors " +
                /* 지적 문장은 호박색이다. 호박색은 한 화면에서 `지금 볼 것`에만 쓴다. */
                (flagged ? "border-amber-line bg-amber-soft" : "border-separator bg-panel")
              }
            >
              <span className={"mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-capsule text-micro tabular-nums " + (flagged ? "font-semibold text-amber-text" : "font-medium text-label-3")}>
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <span
                  className="text-label"
                  style={{ fontSize: `${fontSize}px`, lineHeight: 1.55 }}
                >
                  {s.text}
                </span>
                {(flagged || fixed || modified || !readOnly) && (
                  <div className="mt-1 flex min-h-6 flex-wrap items-center gap-1.5">
                    {/* 반려 때 지적된 문장이다. 아직 그대로면 호박색 채움, 고쳤으면 테두리 칩과 `고침` 점으로 구분한다. */}
                    {flagged && (
                      <>
                        <span className={WB_CHIP_TIGHT + " bg-amber font-semibold text-on-amber"}>지적</span>
                        <span className="text-micro font-medium text-amber-text">아직 그대로</span>
                      </>
                    )}
                    {fixed && (
                      <>
                        <span className={WB_CHIP_TIGHT + " border border-separator-strong text-label-3"}>지적</span>
                        <span className="inline-flex items-center gap-1 text-micro text-label-2">
                          <span className="size-1.5 rounded-capsule bg-ok" aria-hidden="true" />
                          고침
                        </span>
                      </>
                    )}
                    {/* 상태 배지. 동사(`수정`)를 강조색으로 두면 누르는 버튼으로 읽힌다. */}
                    {modified && (
                      <span className={WB_CHIP_TIGHT + " bg-fill-2 text-label-3"} title="AI 초안에서 고친 문장">
                        교정됨
                      </span>
                    )}
                    {/*
                      수정과 삭제. 카드 아래 줄 오른쪽에 둔다.
                      오른쪽 세로 열에 두면 안 보이는 동안에도 폭을 차지해 문장이 좁게 접힌다.
                      행에 마우스나 포커스가 있을 때만 보인다. 읽기 전용이면 그리지 않는다.
                    */}
                    {!readOnly && (
                      <span className={"ml-auto flex shrink-0 items-center gap-0.5 " + REVEAL_ON_HOVER}>
                        <button
                          type="button"
                          data-edit-btn={i}
                          onClick={() => startEdit(i)}
                          title="문장 수정"
                          aria-label={`문장 ${i + 1} 수정`}
                          className="flex h-6 w-6 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          type="button"
                          onClick={() => markDeleted(i)}
                          title="문장 삭제"
                          aria-label={`문장 ${i + 1} 삭제`}
                          className="flex h-6 w-6 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-bad-muted hover:text-bad-text focus:outline-none focus-visible:ring-2 focus-visible:ring-bad/40"
                        >
                          <Trash2 size={14} />
                        </button>
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          </Fragment>
        );
      })}
      {/* 맨 끝 작성 슬롯 */}
      {composing && composeBox(sentences.length + 1)}
      {!readOnly && (
        <button
          type="button"
          onClick={openCompose}
          className="flex w-full items-center justify-center gap-1.5 rounded-card border border-dashed border-separator-strong px-2.5 py-2 text-caption font-medium text-label-3 transition-colors hover:border-label-3 hover:text-label-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <Plus size={14} /> 문장 추가
        </button>
      )}
      {/*
        빈 상태도 이름(굵게)과 안내(작게)로 나눈다.
        결과와 할 일을 한 문장에 넣지 않는다.
        읽기 전용이면 할 일이 없으므로 캡션이 없다는 사실만 쓴다.
      */}
      {sentences.length === 0 && !composing && (
        readOnly ? (
          <p className="px-1 text-micro text-label-3">캡션 없음</p>
        ) : (
          <div className="px-1">
            <p className="text-micro font-medium text-label-3">AI 초안 없음</p>
            <p className="text-micro text-label-3">영상을 보고 문장을 추가합니다.</p>
          </div>
        )
      )}
    </div>
  );
}
