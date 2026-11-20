/**
 * 콘텐츠 클립 탭의 클립 미리보기 창.
 *
 * 왼쪽은 클립 구간만 재생하는 영상이고, 오른쪽은 구간·장면 요약·키워드·화자·음성 인식이다.
 * 채운 버튼은 `클립 보기` 하나다. 영상 상세는 보조 버튼이다.
 */
import { ChevronDown, ChevronUp } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { VideoPlayer } from "./VideoPlayer";
import { buttonClasses } from "@/shared/ui/Button";
import { Chip } from "@/shared/ui/Chip";
import { Modal } from "@/shared/ui/Modal";
import { clipRangeFormatter } from "../lib/clip_range";
import { speakerSpans } from "../lib/speaker_spans";
import type { ClipDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";

/**
 * 긴 글을 정한 줄 수에서 접고, 넘칠 때만 `전체 보기` 버튼을 단다.
 *
 * 장면 요약·음성 인식은 한 클립에 스무 줄을 넘기도 해서 다 펼치면 키워드·화자가 창 아래로 밀린다.
 * 넘치는지는 그린 뒤에 재서, 짧은 글에는 버튼을 달지 않는다.
 */
function ClampedText({ text, lines, className }: { text: string; lines: 4 | 6; className?: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return (
    <>
      <p ref={ref} className={cn(className, !open && (lines === 6 ? "line-clamp-6" : "line-clamp-4"))}>{text}</p>
      {(overflows || open) && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="mt-1 inline-flex items-center gap-1 rounded-control text-caption font-medium text-label-2 hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          {open ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
          {open ? "접기" : "전체 보기"}
        </button>
      )}
    </>
  );
}

/** 클립 한 건의 미리보기 창을 그린다. `clip`이 없으면 닫힌 상태다. */
export function ClipPreviewModal({
  clip, slug, onClose,
}: {
  clip: ClipDTO | null;
  slug: string;
  onClose: () => void;
}) {
  if (!clip) return null;
  const fmt = clipRangeFormatter(clip.end_seconds);
  const keywords = (clip.keywords ?? []).map((k) => String(k.text ?? "")).filter(Boolean);
  const speakers = speakerSpans(clip.speakers ?? [], clip.speeches ?? []);
  const transcript = ((clip.speeches ?? []) as { text?: string }[]).map((s) => s.text?.trim()).filter(Boolean).join(" ");
  const summary = clip.scene_summary?.trim();

  return (
    <Modal
      open
      onClose={onClose}
      /* 두 열이라 기본 폭(lg 720px)보다 넓게 둔다. 영상이 좁아지면 장면을 알아보기 어렵다. */
      className="max-w-[920px]"
      title={
        <>
          {clip.scene != null ? `장면 #${clip.scene}` : "클립"}
          <span className="mt-0.5 block text-footnote font-normal text-label-3">{clip.video_title ?? ""}</span>
        </>
      }
      footer={
        <>
          <Link to={`/p/${slug}/videos/${clip.video_id}`} className={buttonClasses("secondary", "sm")}>
            영상 상세
          </Link>
          <Link to={`/p/${slug}/videos/${clip.video_id}/clips`} className={buttonClasses("primary", "sm")}>
            클립 보기
          </Link>
        </>
      }
    >
      {/*
        두 열을 위쪽에 맞춘다. 늘이면 오른쪽 글이 긴 클립에서 영상 영역이 그 높이만큼 검은 면으로 늘어난다.
        영상은 창 본문을 스크롤해도 제자리에 둔다(`sticky`).
      */}
      <div className="grid gap-6 md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] md:items-start">
        <div className="min-w-0 overflow-hidden rounded-card bg-ws-stage md:sticky md:top-0">
          <VideoPlayer
            key={clip.clip_id}
            videoId={clip.video_id}
            startSeconds={clip.start_seconds}
            endSeconds={clip.end_seconds}
            hideTitle
          />
        </div>

        <div className="min-w-0 space-y-4 text-footnote">
          <dl className="space-y-2.5 border-b border-separator pb-4">
            <div className="flex items-center gap-4">
              <dt className="w-16 shrink-0 text-caption font-medium text-label-3">구간</dt>
              <dd className="font-mono tabular-nums text-label">
                {fmt(clip.start_seconds, clip.end_seconds)} · {(clip.end_seconds - clip.start_seconds).toFixed(1)}초
              </dd>
            </div>
          </dl>

          {/* 값이 있는 절만 그린다. 빈 머리글만 남으면 분석 결과가 무엇인지 헷갈린다. */}
          {summary && (
            <section>
              <h3 className="text-caption font-semibold text-label-3">장면 요약</h3>
              <ClampedText key={clip.clip_id} text={summary} lines={6} className="mt-1 text-body leading-relaxed text-label" />
            </section>
          )}
          {keywords.length > 0 && (
            <section>
              <h3 className="text-caption font-semibold text-label-3">키워드</h3>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {keywords.map((k, i) => <Chip key={`${k}-${i}`}>{k}</Chip>)}
              </div>
            </section>
          )}
          {speakers.length > 0 && (
            <section>
              <h3 className="text-caption font-semibold text-label-3">화자</h3>
              <ul className="mt-1 space-y-0.5">
                {speakers.map((sp) => (
                  <li key={sp.name} className="flex gap-2 text-label">
                    {sp.name}
                    {sp.start != null && sp.end != null && (
                      <span className="font-mono tabular-nums text-label-3">{fmt(sp.start, sp.end)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {transcript && (
            <section>
              <h3 className="text-caption font-semibold text-label-3">음성 인식</h3>
              <ClampedText key={clip.clip_id} text={transcript} lines={4} className="mt-1 leading-relaxed text-label-2" />
            </section>
          )}
          {!summary && keywords.length === 0 && speakers.length === 0 && !transcript && (
            <p className="text-label-3">분석 결과 없음</p>
          )}
        </div>
      </div>
    </Modal>
  );
}
