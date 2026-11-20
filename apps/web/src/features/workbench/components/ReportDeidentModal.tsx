import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Drawer } from "@/shared/ui/Drawer";
import { FIELD_BASE } from "@/shared/ui/field";
import { Modal } from "@/shared/ui/Modal";
import { cn } from "@/shared/lib/cn";
import { fmtTimecode } from "@/shared/lib/fmtSecs";
import { SECTION_BADGE } from "./label_parts";

/**
 * 비식별화 미흡 신고 창. 라벨링·검수 워크벤치가 쓴다. 신고하면 작업의 보유가 풀린다.
 *
 * 서버(`work.service.report_deident_miss`)는 클립을 비식별화 신고 상태로 바꾸고 신고한 사람의 점유를 푼다.
 * 라벨링 점유면 임시저장(`draft`)이 지워지고, 검수 점유면 판정 초안(`review_draft`)이 지워진다.
 * 그래서 누르기 전에 지워지는 초안을 첫 줄에서 알린다.
 *
 * 여는 버튼은 `비식별화 신고`, 창 제목은 `비식별화 미흡 신고`, 제출 버튼은 `신고`다.
 * `보고`와 `신고`를 섞어 쓰지 않는다.
 *
 * presentation: 워크벤치는 "drawer"(오른쪽에서 열려 작업 화면을 유지한다),
 * 목록·페이지는 "modal"(기본값)을 쓴다.
 */
export function ReportDeidentModal({
  open,
  onClose,
  onSubmit,
  pending,
  presentation = "modal",
  scope = "label",
  positionMs,
  clipName,
  clipRange,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => void;
  pending: boolean;
  presentation?: "modal" | "drawer";
  /** 신고하는 사람이 보유한 작업의 종류. 지워지는 초안이 다르다(라벨링은 임시저장, 검수는 판정 초안). */
  scope?: "label" | "review";
  /** 함께 기록되는 재생 위치(클립 기준 ms). 창을 열 때 잡은 값이고, 제출도 이 값을 보낸다. */
  positionMs?: number | null;
  /** 신고하는 클립이 속한 영상의 제목. 신고 대상을 표에 적는다. */
  clipName?: string | null;
  /** 신고하는 클립의 원본 구간(`2:20–3:24`). 같은 영상의 다른 클립과 구분한다. */
  clipRange?: string | null;
  /** 신고 실패 문장. 일시 오류로 서랍이 열린 채 남을 때 본문 끝에 표시한다. */
  error?: { message: string; hint?: string | null } | null;
}) {
  const [reason, setReason] = useState("");
  /* 열 때마다 비운다. 신고하면 다음 작업으로 넘어가도 이 창은 그대로 마운트되어 있어,
     비우지 않으면 앞 작업에 적은 누락 내용이 다음 작업의 신고 창에 남는다. */
  useEffect(() => {
    if (open) setReason("");
  }, [open]);
  const result =
    scope === "review"
      ? "신고하면 검수 작업의 보유가 풀리고 판정 초안은 지워집니다."
      : "신고하면 작업의 보유가 풀리고 임시저장한 내용은 지워집니다.";
  const position = positionMs != null ? fmtTimecode(positionMs / 1000) : null;
  /* 결과는 굵게, 그 뒤에 일어나는 일은 작게 쓴다. 재생 위치는 문장에 넣지 않고 표의 값으로 뺀다.
     필수 표시는 별표 대신 `필수` 글자로 적는다. 화면 낭독기도 그대로 읽는다.
     모양은 `선택` 표시와 같은 배지다. 필수만 빨간 글자로 두면 입력 전부터 오류처럼 보인다. */
  const body = (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-callout font-semibold text-label">{result}</p>
        <p className="text-footnote text-label-2">
          <span className="block">관리자가 신고를 확인할 때까지 이 작업은 멈춥니다.</span>
          <span className="block">관리자가 재개하면 이 작업은 대기열로 돌아옵니다.</span>
        </p>
      </div>
      <dl className="space-y-2 rounded-card border border-separator bg-canvas-2 px-4 py-3 text-footnote">
        {clipName && (
          <div className="flex items-baseline justify-between gap-4">
            <dt className="shrink-0 text-label-3">영상</dt>
            <dd className="truncate text-right text-label">{clipName}</dd>
          </div>
        )}
        {clipRange && (
          <div className="flex items-baseline justify-between gap-4">
            <dt className="shrink-0 text-label-3">클립</dt>
            <dd className="text-right font-mono tabular-nums text-label">{clipRange}</dd>
          </div>
        )}
        <div className="flex items-baseline justify-between gap-4">
          <dt className="shrink-0 text-label-3">재생 위치</dt>
          <dd className="text-right text-label">
            {position ? (
              <>
                <span className="font-mono font-semibold tabular-nums">{position}</span>
                <span className="ml-1.5 text-label-3">함께 기록</span>
              </>
            ) : (
              <span className="text-label-3">지금 재생 위치를 함께 기록</span>
            )}
          </dd>
        </div>
      </dl>
      <label className="block space-y-1.5">
        <span className="flex items-center gap-1.5 text-caption font-semibold text-label">
          누락 내용
          <span className={SECTION_BADGE}>필수</span>
        </span>
        <textarea
          required
          aria-required="true"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={4}
          autoFocus
          placeholder="예: 0:12 인물 얼굴 비식별화 누락"
          className={cn(FIELD_BASE, "resize-none px-3 py-2 text-footnote")}
        />
      </label>
      {error && (
        <div role="alert" className="rounded-card border border-bad/40 bg-bad-muted px-3 py-2 text-footnote">
          <p className="font-semibold text-label">{error.message}</p>
          {error.hint && <p className="text-label-2">{error.hint}</p>}
        </div>
      )}
    </div>
  );

  /* 액션 줄은 창이 그린다(`footer`). 구분선 아래에 고정된다.
     아이콘도 슬롯으로 준다. 제목 문자열 안에 넣으면 드로어 제목의 `truncate`가 아이콘까지 자른다.
     아이콘은 머리 줄 신고 버튼과 같은 방패이고 원형 배지는 중립색으로 둔다.
     흑연색 면에서는 경고색이 호박색(`지금 볼 것`)과 거의 같아 구분되지 않는다. 초안이 지워진다는 사실은 본문 첫 줄이 알린다. */
  const footer = (
    <>
      <Button variant="secondary" size="sm" onClick={onClose}>취소</Button>
      <Button size="sm" disabled={!reason.trim() || pending} onClick={() => onSubmit(reason.trim())}>
        {pending ? "신고 중…" : "신고"}
      </Button>
    </>
  );
  const icon = <ShieldAlert size={16} aria-hidden="true" />;

  if (presentation === "drawer") {
    return (
      <Drawer
        open={open}
        onClose={onClose}
        title="비식별화 미흡 신고"
        icon={icon}
        tone="neutral"
        footer={footer}
        className="max-w-md"
      >
        {body}
      </Drawer>
    );
  }

  return (
    <Modal open={open} onClose={onClose} title="비식별화 미흡 신고" icon={icon} tone="neutral" footer={footer}>
      {body}
    </Modal>
  );
}
