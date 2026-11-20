import { DiffText } from "@/shared/ui/DiffText";
import { useState } from "react";
import { Check, Flag, Pencil, RotateCcw, Trash2 } from "lucide-react";

import type { CaptionSentence } from "../lib/caption";
import { wordDiff } from "@/shared/lib/diff";
import { WB_CHIP_TIGHT } from "./chip";
import type { components } from "@reborn/contracts";

/** 자동 검수 지적 한 건. 서버 `caption_qc`가 준 값을 그대로 쓴다. */
type QcFinding = components["schemas"]["QcFinding"];

/**
 * 검수 캡션의 문장별 판정 상태. 확인(`ok`), 수정(`edit`, 내용 교체), 삭제(`del`, 문장 제거), 반려(`flag`)가 있다.
 * `text`는 `edit`일 때의 수정본이다. 삭제한 문장은 최종 캡션에서 빠진다.
 */
export type SentenceMark = { status: "ok" | "edit" | "flag" | "del"; text?: string };

const ACTIONS = [
  // 이름은 괄호 설명 없이 한 낱말로 쓴다.
  // 괄호를 쓰면 화면 낭독기가 읽는 이름에도 괄호가 들어간다.
  { status: "ok" as const, Icon: Check, on: "bg-ok-muted text-ok-text", idle: "text-label-3 hover:bg-ok-muted hover:text-ok-text", title: "확인" },
  { status: "edit" as const, Icon: Pencil, on: "bg-accent-muted text-accent", idle: "text-label-3 hover:bg-accent-muted hover:text-accent", title: "수정" },
  { status: "del" as const, Icon: Trash2, on: "bg-fill-3 text-label-3", idle: "text-label-3 hover:bg-bad-muted hover:text-bad-text", title: "삭제" },
  { status: "flag" as const, Icon: Flag, on: "bg-bad-muted text-bad-text", idle: "text-label-3 hover:bg-bad-muted hover:text-bad-text", title: "반려" },
];

/**
 * 문장 카드에 붙는 자동 검수 지적을 그린다.
 *
 * 지적은 `sentence_id`를 담고 오고, 카드도 `s.id`로 그리므로 둘을 이어 붙일 수 있다.
 * 지적이 화면 아래 카드에만 있으면 검수자는 문장 번호를 읽고 위쪽 스크롤 영역에서 그 카드를 다시 찾아야 한다.
 *
 * 배지는 누를 수 없다. 자동 검수는 판정하지 않고 사람이 판정하기 전에 보는 근거만 준다.
 * 그래서 배지로 문장을 바로 `반려` 표시하는 지름길을 두지 않는다.
 *
 * 메시지는 hover 없이 바로 보인다. 아래 카드에 같은 지적을 다시 표시하지 않으므로
 * hover로만 보이면 키보드·터치 사용자는 읽을 수 없다.
 */
function QcNote({ findings }: { findings: QcFinding[] }) {
  // 색은 지적의 출처(규칙 또는 모델)를 구분한다. 규칙과 모델은 신뢰도가 다르다.
  // 아래 카드의 `모델`/`규칙 검사` 구분과 같은 기준이다.
  const fromModel = findings.some((f) => f.source === "model");
  return (
    <p className="mt-2 flex items-start gap-2 rounded-control border border-separator bg-canvas px-2.5 py-2 text-footnote text-label">
      <span
        className={
          WB_CHIP_TIGHT + " mt-px shrink-0 "
          + (fromModel ? "bg-accent-muted text-accent" : "text-label-3")
        }
      >
        품질검수
      </span>
      <span className="min-w-0">{findings.map((f) => f.message).join(" · ")}</span>
    </p>
  );
}

/** 간단한 단어 단위 diff를 그린다. 원본 대비 추가는 초록, 삭제는 빨간 취소선으로 보인다. */
function CompactDiff({ from, to }: { from: string; to: string }) {
  return (
    <div className="mt-1.5 rounded-card bg-canvas px-2.5 py-1.5 text-caption leading-relaxed">
      <span className="mr-1 text-nano text-label-3">원본 대비</span>
        <DiffText ops={wordDiff(from, to)} />
    </div>
  );
}

/**
 * 검수 캡션의 문장별 판정 뷰를 그린다.
 *
 * 각 문장을 번호 카드로 그리고, 카드 아래 줄에 판정 상태·확인·수정·삭제·반려 도구를 둔다.
 * 확인은 초록 점, 반려는 빨강 카드다. 수정은 그 카드 위치에서 인라인으로 편집하고
 * 원본 대비 간단한 diff를 보인다. 삭제한 문장은 취소선으로 흐리게 보인다.
 * 상태는 상위(검수 워크벤치)가 `marks`로 관리하고 반려 사유, 수정 캡션, 판정 제안에 쓴다.
 * 자동 검수 지적(`findings`)은 판정 근거라서 판정하는 문장 아래에 붙인다.
 */
export function SentenceTriage({
  sentences,
  fontSize,
  marks,
  onMark,
  findings,
  readOnly = false,
}: {
  fontSize: number;
  /** 문장 배열. 라벨러가 저장한 객체를 그대로 받고, 여기서 다시 나누지 않는다. */
  sentences: CaptionSentence[];
  /** 판정은 문장 id에 붙는다. 인덱스에 붙이면 문장 수가 달라지는 순간 어긋난다. */
  marks: Map<string, SentenceMark>;
  /** mark가 null이면 해당 문장의 판정을 해제한다. */
  onMark: (id: string, mark: SentenceMark | null) => void;
  /**
   * 문장 id별로 그 문장에 붙은 자동 검수 지적.
   *
   * 어느 지적이 어느 문장에 붙는지는 상위가 정한다. 아래 `검수 결과` 카드도 같은 판정을
   * 해야 한다(그 카드에는 문장에 붙지 않는 지적만 남긴다). 두 곳이 각자 판정하면 지적 하나가
   * 두 번 나오거나 어느 쪽에도 나오지 않는다.
   */
  findings?: Map<string, QcFinding[]>;
  /**
   * 판정 버튼을 그리지 않으면 true. 검수를 보유하지 않은 사람이 볼 때 쓴다.
   *
   * 서버는 판정을 `review_claimed_by`로 막는데 화면은 읽기 권한(reviewer 이상, super_admin)으로 열린다.
   * 버튼을 그리면 관리자가 남의 큐를 열었을 때 누를 때마다 초안 저장이 403으로 실패하므로 그리지 않는다.
   */
  readOnly?: boolean;
}) {
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  // `— 없음`처럼 대시를 붙이지 않는다. 값 위치의 `—`와 겹쳐 기호가 둘이 된다.
  if (!sentences.length) return <p className="text-caption text-label-3">없음</p>;

  const startEdit = (i: number) => {
    setDraft(marks.get(sentences[i].id)?.text ?? sentences[i].text);
    setEditIdx(i);
  };
  const commitEdit = (i: number) => {
    const t = draft.trim();
    onMark(sentences[i].id, t && t !== sentences[i].text ? { status: "edit", text: t } : null);
    setEditIdx(null);
  };

  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => {
        const m = marks.get(s.id);
        const notes = findings?.get(s.id);

        if (editIdx === i) {
          const changed = !!draft.trim() && draft.trim() !== s.text;
          return (
            <div key={s.id} className="rounded-card border border-accent/40 bg-accent-subtle px-2.5 py-2">
              <div className="mb-1.5 flex items-center gap-1.5 text-micro font-medium text-accent">
                <Pencil size={12} /> 문장 {i + 1} 수정
              </div>
              {/*
                편집 중에도 지적을 옆에 보여 준다.
                편집창은 그 지적을 고치려고 여는 것이므로 지적이 함께 보여야 한다.
              */}
              {notes && notes.length > 0 && (
                <div className="mb-1.5">
                  <QcNote findings={notes} />
                </div>
              )}
              <textarea
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={2}
                className="w-full resize-none rounded-field border border-separator-strong bg-panel px-2.5 py-1.5 leading-relaxed text-label transition-colors focus:border-label-3 focus:outline-none"
                style={{ fontSize: `${fontSize}px`, lineHeight: 1.5 }}
              />
              {changed && <CompactDiff from={s.text} to={draft} />}
              <div className="mt-2 flex justify-end gap-1.5">
                <button
                  type="button"
                  onClick={() => setEditIdx(null)}
                  className="rounded-control px-2.5 py-1 text-micro font-medium text-label-2 transition-colors hover:bg-fill focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                >
                  취소
                </button>
                <button
                  type="button"
                  onClick={() => commitEdit(i)}
                  className="rounded-control bg-accent-fill px-3 py-1 text-micro font-semibold text-accent-fg transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-1"
                >
                  확인
                </button>
              </div>
            </div>
          );
        }

        if (m?.status === "del") {
          return (
            <div key={s.id} className="flex items-center gap-2 rounded-card border border-separator bg-fill-2 px-2.5 py-2">
              <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-capsule bg-fill-3 text-micro font-medium text-label-4">
                {i + 1}
              </span>
              <span className="flex-1 text-label-4 line-through" style={{ fontSize: `${fontSize}px`, lineHeight: 1.55 }}>
                {s.text}
              </span>
              <span className="shrink-0 rounded-control bg-fill-3 px-1.5 py-0.5 text-nano font-medium text-label-3">삭제됨</span>
              {/*
                보유한 사람의 초안은 보이지만 되돌리기는 보유한 사람만 할 수 있다.
                읽기 전용에서는 그리지 않는다.
              */}
              {!readOnly && (
              <button
                type="button"
                onClick={() => onMark(s.id, null)}
                title="되돌리기"
                aria-label={`문장 ${i + 1} 삭제 되돌리기`}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-label-4 transition-colors hover:bg-fill hover:text-label-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              >
                <RotateCcw size={14} />
              </button>
              )}
            </div>
          );
        }

        /* 카드는 판정 색으로 칠한다. 지적은 빨강, 확인은 초록 점만 둔다. */
        const cardBg =
          m?.status === "flag"
            ? "border-bad/60 bg-bad-subtle"
            : m?.status === "edit"
              ? "border-separator-strong bg-panel"
              : "border-separator bg-panel";
        const statusText: Record<string, { label: string; dot: string }> = {
          ok: { label: "확인", dot: "bg-ok" },
          flag: { label: "지적", dot: "bg-bad" },
          edit: { label: "수정", dot: "bg-label-2" },
        };
        const st = m ? statusText[m.status] : null;

        return (
          <div key={s.id} className={"group rounded-card border px-3 py-2.5 " + cardBg}>
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center text-micro font-medium tabular-nums text-label-3">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 text-label" style={{ fontSize: `${fontSize}px`, lineHeight: 1.55 }}>
                {m?.status === "edit" ? m.text ?? s.text : s.text}
              </span>
            </div>
            {/* 아래 줄: 판정 상태, 판정 도구. 도구를 오른쪽 세로 열에 두면 문장이 좁게 접힌다. */}
            <div className="mt-1.5 flex min-h-7 flex-wrap items-center gap-2 pl-[26px]">
              {st ? (
                <span className="inline-flex items-center gap-1 text-micro font-medium text-label-2">
                  <span className={"size-1.5 rounded-capsule " + st.dot} aria-hidden="true" />
                  {st.label}
                </span>
              ) : (
                !readOnly && <span className="text-micro text-label-3">미검토</span>
              )}
              {!readOnly && (
                <span className="ml-auto flex items-center gap-0.5">
                  {ACTIONS.map((a) => (
                    <button
                      key={a.status}
                      type="button"
                      title={a.title}
                      /*
                        접근성 이름에 문장 번호를 넣는다. 그렇지 않으면 보조 기술에는 `확인 · 수정 · 삭제 · 반려`가
                        문장 수만큼 똑같이 반복된다.
                      */
                      aria-label={`문장 ${i + 1} ${a.title}`}
                      aria-pressed={m?.status === a.status}
                      onClick={() =>
                        a.status === "edit" ? startEdit(i) : onMark(s.id, m?.status === a.status ? null : { status: a.status })
                      }
                      className={
                        "flex h-7 w-7 items-center justify-center rounded-control transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-focus " +
                        (m?.status === a.status ? a.on : a.idle)
                      }
                    >
                      <a.Icon size={14} />
                    </button>
                  ))}
                </span>
              )}
            </div>
            {/* 자동 검수 지적은 문장 바로 아래에 붙인다. 아래 카드에서 `(3번째 문장)`을 읽고 다시 찾아 올라가지
                않게 한다. 삭제 표시한 문장에는 붙이지 않는다. 최종 캡션에서 빠지므로 이미 처리된 지적이다. */}
            {notes && notes.length > 0 && <QcNote findings={notes} />}
          </div>
        );
      })}
    </div>
  );
}
