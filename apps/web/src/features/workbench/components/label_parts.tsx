/**
 * 라벨링 워크벤치의 표시용 컴포넌트. `LabelWorkbench`가 쓰는 상수와 작은 컴포넌트를 모은다.
 * 로직은 `useLabelWorkbench`에 있다. 이 파일의 컴포넌트는 props를 받아 그리기만 하고 상태가 거의 없다.
 */
import { type ReactNode } from "react";
import type { components } from "@reborn/contracts";

import { airedDate, VIDEO_META_FIELDS, VIDEO_META_NAME, VIDEO_META_OWNER_NOTE } from "@/lib/video_meta";
import { helpColumns, LABEL_HELP } from "../lib/shortcuts";

import { WbShortcutsDialog } from "./WbShortcutsDialog";

/** 캡션 글자 크기(px). 라벨링과 검수의 문장 카드가 같은 크기로 그린다. */
export const CAPTION_FONT_SIZE = 15;

/** 캡션 길이를 세는 단위. 프로젝트 설정과 같은 문장이라 공용 위치에서 가져와 다시 내보낸다. */
export { CAPTION_LENGTH_UNIT } from "@/shared/lib/caption_length";

/** 라벨링 워크벤치의 단축키 창을 그린다. 왼쪽은 재생, 오른쪽은 문장 편집과 저장·제출이다. */
export function WbShortcutsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  /* 줄을 직접 적지 않는다. 처리기가 읽는 표(`lib/shortcuts.ts`의 `LABEL_HELP`)를 그대로 그린다. */
  return (
    <WbShortcutsDialog
      open={open}
      onClose={onClose}
      title="단축키 · 라벨링"
      columns={helpColumns(LABEL_HELP)}
      footnote="문장 편집 중에는 ⌘↵가 편집 확정입니다."
    />
  );
}

/**
 * 저장 상태 한 줄을 그린다. 라벨링 임시저장과 검수 초안이 함께 쓴다.
 *
 * 저장에 실패하면 실패로 표시한다. `onRetry`를 주면 실패 표시가 다시 시도하는 버튼이 된다.
 * 실패를 "저장됨"으로 표시하면 토스트가 사라진 뒤 실패한 흔적이 없어, 입력한 지적과 사유를 잃은 줄 모른다.
 *
 * @param idleText 아직 저장한 적이 없을 때의 문구. 주기인지 남은 시간인지 헷갈리지 않게 문장으로 쓴다.
 *   라벨링은 30초(`useLabelWorkbench`의 타이머), 검수는 2초 간격으로 자동 저장한다.
 */
export function WbSaveStatus({
  state,
  at,
  idleText = "입력 후 30초 뒤 자동 저장",
  onRetry,
}: {
  state: "idle" | "saving" | "saved" | "error";
  at: string | null;
  idleText?: string;
  onRetry?: () => void;
}) {
  // 자동 저장 표시는 작은 점(●)과 작은 글자로 눈에 덜 띄게 둔다. 상태는 점 색으로 구분한다.
  // 점이 작아 muted(옅은 배경색)로는 잘 보이지 않아서 `saving`과 같은 진한 단계(`ok`·`bad`)를 쓴다.
  // 없는 유틸 클래스 이름을 쓰면 점이 투명해져 저장 성공과 실패를 구분할 수 없다.
  const map = {
    saving: ["bg-warn", "저장 중", "text-label-2"],
    saved: ["bg-ok", at ? `저장 완료 ${at}` : "저장 완료", "text-label-3"],
    error: ["bg-bad", "저장 실패", "text-bad-text"],
    idle: ["bg-label-3", idleText, "text-label-3"],
  } as const;
  const [dot, text, textCls] = map[state];
  if (state === "error" && onRetry) {
    return (
      <button
        type="button"
        onClick={onRetry}
        aria-live="polite"
        className={"flex items-center gap-1.5 whitespace-nowrap rounded-control px-1 text-micro hover:underline " + textCls}
      >
        <span className={"h-1.5 w-1.5 rounded-capsule " + dot} aria-hidden="true" />
        {text} · 다시 시도
      </button>
    );
  }
  return (
    <span className={"flex items-center gap-1.5 whitespace-nowrap text-micro " + textCls} aria-live="polite">
      <span className={"h-1.5 w-1.5 rounded-capsule " + dot} />
      {text}
    </span>
  );
}

/** 절 제목·입력란 이름 옆 작은 배지(`필수`·`선택`·`읽기 전용`). 제목 옆 괄호 설명 대신 쓴다. */
export const SECTION_BADGE =
  "rounded-mark border border-separator-strong px-1.5 py-px text-micro font-medium text-label-3";

/**
 * 영상 메타 절을 읽기 전용으로 그린다. 라벨링과 검수가 같은 모양으로 그린다.
 *
 * 방송사·프로그램명·장르·방영일은 영상 단위 값이라 관리자가 영상 상세에서 넣고 클립이 물려받는다.
 * 그래서 입력란 대신 누가 채우는지를 쓴다.
 */
export function WbVideoMeta({
  meta,
}: {
  meta: NonNullable<components["schemas"]["ClipDTO"]["video_meta"]>;
}) {
  return (
    <WbSection
      title={
        <>
          영상 메타
          <span className={SECTION_BADGE}>읽기 전용</span>
        </>
      }
    >
      <dl className="space-y-1.5 text-footnote">
        {VIDEO_META_FIELDS.map((k) => {
          const val = k === "aired_at" ? airedDate(meta.aired_at) : meta[k];
          return (
            <div key={k} className="flex items-center justify-between gap-2">
              <dt className="shrink-0 text-label-3">{VIDEO_META_NAME[k]}</dt>
              <dd className="truncate text-label-2">{val || <span className="text-label-4">—</span>}</dd>
            </div>
          );
        })}
      </dl>
      <p className="mt-2 text-micro text-label-3">{VIDEO_META_OWNER_NOTE}</p>
    </WbSection>
  );
}

/**
 * 워크벤치 레일의 절을 그린다. 머리 줄(이름·오른쪽 수)과 본문으로 이뤄진다.
 *
 * 절 사이는 구분선 대신 여백으로 가른다. 이름은 12/600, 오른쪽 수는 13 보조색이다.
 */
export function WbSection({
  title,
  aside,
  children,
}: {
  title: ReactNode;
  /** 머리 줄 오른쪽 내용(`문장 4개`). */
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="px-5 pt-5">
      <div className="flex min-h-6 items-center justify-between gap-2 pb-2.5">
        <h4 className="flex items-center gap-1.5 text-caption font-semibold text-label-2">{title}</h4>
        {aside && <div className="flex shrink-0 items-center gap-2 text-footnote text-label-3">{aside}</div>}
      </div>
      <div>{children}</div>
    </section>
  );
}
