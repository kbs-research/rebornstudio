/**
 * 클립이 하나도 없을 때의 안내. 비어 있는 이유를 알리고, 구간을 직접 추가하게 한다.
 *
 * 비어 있는 이유는 준비 타임라인의 `scene_split` 단계에서 가져온다. 이 단계가 영상을 나누는 주체와
 * 진행 상태를 이미 담고 있으므로 이 부품에서 분석 스택을 다시 추측하지 않는다.
 * 특정 스택을 가정한 고정 문구는 플랫폼이 직접 샷을 나누는 스택(`ncp-shots`)에서 틀린다.
 * 분할 기능이 없는 스택에서는 사람이 구간을 넣어야 하므로 직접 추가가 첫 동작이다.
 *
 * 영상이 막혔거나 분석 요청 전이면 준비 단계의 문구를 먼저 쓴다(`video_stage.stageEmpty`).
 * `scene_split`만 보고 고르면 변환이 실패한 영상이 장면 분할 대기로 보이고, 재생할 수 없는 영상에 `구간 추가`를 권하게 된다.
 */
import { type RefObject, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { MediaPlayerInstance } from "@vidstack/react";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { Button, buttonClasses } from "@/shared/ui/Button";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { UnitInput } from "@/shared/ui/UnitInput";
import { useToast } from "@/shared/ui/Toast";
import type { ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { fmtClock as clock } from "@/shared/lib/fmtSecs";
import type { components } from "@reborn/contracts";

import { addManualClip } from "../api";
import { type AddMode, type NextStep, stageEmpty } from "../lib/video_stage";

type Entry = components["schemas"]["PrepTimelineEntry"];
type VideoDTO = components["schemas"]["VideoDTO"];

/**
 * 장면 분할 단계의 상태로 비어 있는 이유를 고른다. 단계가 없으면 아직 클립이 없다고만 쓴다.
 *
 * `running`·`pending`·`skipped`는 기다리면 클립이 생기고, `failed`와 `ok`(클립 0개)는 기다려도 생기지 않는다.
 * `waiting`으로 이 차이를 돌려주어 직접 추가를 주 동작으로 권할지 정한다.
 * 실패는 결과 한 줄과 사유 한 줄로 나눈다.
 */
function reason(e: Entry | undefined): { text: string; detail?: string; waiting: boolean } {
  if (!e) return { text: "아직 클립이 없습니다.", waiting: false };
  switch (e.status) {
    case "running":
      return { text: "장면을 나누는 중입니다.", waiting: true };
    case "pending":
      return { text: "장면 분할을 기다리는 중입니다.", waiting: true };
    case "failed":
      // 실패는 기다려도 풀리지 않는다. 원인을 고칠 수 있게 사유를 그대로 보여 준다.
      return { text: "장면 분할에 실패했습니다.", detail: e.error ?? undefined, waiting: false };
    case "skipped":
      // 분석기가 분석하면서 영상을 나누는 스택이다. 분석이 끝나면 클립이 생기므로 기다리는 경우로 본다.
      return { text: e.skipped_reason ?? "분석할 때 함께 나뉩니다.", waiting: true };
    default:
      // `ok`인데 클립이 0개인 경우. 분할은 끝났지만 구간이 나오지 않았으므로 기다려도 생기지 않는다.
      return { text: "장면 분할은 끝났지만 만들어진 구간이 없습니다.", waiting: false };
  }
}

/**
 * 현재 재생 위치(초)를 플레이어 안의 `<video>`에서 읽는다.
 *
 * Vidstack 인스턴스의 `currentTime`은 재생 중에도 0을 돌려주는 순간이 있어서 `useClipPlayhead`와 같은 방식으로 읽는다.
 * 읽지 못하면 `null`을 돌려준다. 0은 영상 맨 앞이라는 값이라 읽지 못한 값이 사용자가 지정한 값처럼 들어간다.
 */
function nowSec(ref?: RefObject<MediaPlayerInstance | null>): number | null {
  const v = ref?.current?.el?.querySelector("video") as HTMLVideoElement | null;
  return v && Number.isFinite(v.currentTime) ? v.currentTime : null;
}

/** 클립이 없을 때의 안내와 구간 추가 모달을 그린다. */
export function EmptyClips({
  projectId, videoId, slug, stage, sceneSplit, onAdded, playerRef, canAdd = false, minSeconds,
}: {
  projectId: string;
  videoId: string;
  /** 구간의 최소 길이(초). 서버가 프로젝트 클립 길이 하한으로 준 값(`ProjectDTO.clip_min_seconds`)이다. */
  minSeconds: number;
  /** 영상 등록 화면으로 잇는 링크에 쓰는 프로젝트 슬러그. */
  slug?: string;
  /** 서버가 정한 준비 단계. 막혔거나 분석 요청 전이면 이 값이 빈 상태의 이유를 정한다. */
  stage?: Pick<VideoDTO, "prep_stage" | "prep_blocked_stage">;
  sceneSplit?: Entry;
  onAdded: () => void;
  /** 위쪽 플레이어. 재생 위치로 구간을 지정하려고 받는다. 없으면 숫자 입력만 남는다. */
  playerRef?: RefObject<MediaPlayerInstance | null>;
  /**
   * 구간을 추가할 수 있으면 true. 서버(`create_manual`)와 같은 판정이다(super_admin·project_admin).
   * 다른 역할에는 비어 있는 이유만 보인다. 버튼을 보이면 눌렀을 때 이유를 알 수 없는 실패를 받는다.
   */
  canAdd?: boolean;
}) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  /**
   * 창을 연 순간의 재생 위치(초). 창이 열린 동안 뒤의 플레이어는 조작할 수 없으므로 한 번 읽어 고정한다.
   */
  const [paused, setPaused] = useState<number | null>(null);
  const openAdd = () => {
    setPaused(nowSec(playerRef));
    setOpen(true);
  };

  const add = useMutation({
    mutationFn: () => addManualClip(projectId, videoId, Number(start), Number(end)),
    onSuccess: () => {
      setOpen(false); setStart(""); setEnd("");
      onAdded();
      toast("success", "구간을 추가했습니다");
    },
    // 서버가 막은 이유는 공용 오류 사전(`errorText`)이 문구로 바꾼다.
    onError: (e: unknown) => {
      const t = errorText((e as ApiError | null)?.detail, { message: "구간을 추가하지 못했습니다" });
      toast("error", t.message, t.hint);
    },
  });

  /*
    준비 단계가 이유를 정하면 그 이유를 쓰고, 아니면 장면 분할 단계를 본다.
    분석 요청·복구·재시도는 관리자만 할 수 있어서 관리자 판정(`canAdd`)이 다음 할 일도 정한다.
  */
  const staged = stage ? stageEmpty(stage, canAdd) : null;
  const split = reason(sceneSplit);
  const text = staged?.text ?? split.text;
  const detail = staged ? staged.detail : split.detail;
  const next: NextStep = staged?.next ?? null;
  const addMode: AddMode = staged?.add ?? (split.waiting ? "secondary" : "primary");
  const ordered = start !== "" && end !== "" && Number(end) > Number(start);
  // 최소 길이보다 짧은 구간은 서버가 거절한다. 만든 구간은 지울 방법이 없으므로 누르기 전에 막는다.
  const tooShort = ordered && Number(end) - Number(start) < minSeconds;
  const valid = ordered && !tooShort;

  return (
    /*
      점선 상자 안에 굵은 머리글(`클립 없음`)과 이유 한 줄, 오른쪽에 할 일 버튼을 둔다.
      막힌 영상은 준비 진행이 이 카드 바로 위에 펼쳐져 있어 준비 진행으로 가는 버튼은 두지 않는다.
      주 동작인지는 안내 문장 대신 버튼 색으로 구분한다.
    */
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-control border border-dashed border-separator-strong px-4 py-3.5">
      <div className="min-w-0 flex-1">
        <h3 className="text-footnote font-semibold text-label">클립 없음</h3>
        <p className="mt-0.5 text-footnote text-label-2">{text}</p>
        {detail && <p className="mt-0.5 text-caption text-label-3">{detail}</p>}
      </div>
      {((next === "ingest" && slug) || (canAdd && addMode !== "hidden")) && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {next === "ingest" && slug && (
            <Link to={`/p/${slug}/pipeline?stage=ingest`} className={buttonClasses("secondary", "sm")}>
              영상 등록 열기
            </Link>
          )}
          {canAdd && addMode !== "hidden" && (
            /* 생성 버튼은 `{대상} 추가` 문구와 `Plus` 아이콘을 쓴다. */
            <Button size="sm" variant={addMode === "primary" && !next ? "primary" : "secondary"} onClick={openAdd}>
              <Plus size={14} strokeWidth={2.5} aria-hidden="true" />
              구간 추가
            </Button>
          )}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="구간 추가"
        /* 구간을 만드는 중립적인 창이라 배지를 달지 않는다. 액션 줄은 `footer`에 고정한다. */
        footer={
          <>
            <Button size="sm" variant="secondary" onClick={() => setOpen(false)}>취소</Button>
            <Button size="sm" onClick={() => add.mutate()} disabled={!valid || add.isPending}>
              {add.isPending ? "추가 중…" : "추가"}
            </Button>
          </>
        }
      >
        {/*
          안내는 한 줄로 두고, 멈춘 위치를 넣는 방법은 아래 상자의 버튼으로 보여 준다.
        */}
        <p className="text-footnote text-label-2">캡션은 라벨링 단계에서 작성합니다.</p>
        {playerRef && (
          paused === null ? (
            <p className="mt-3 text-caption text-label-3">재생 위치를 읽지 못했습니다. 시작과 끝을 숫자로 입력합니다.</p>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-control bg-canvas-2 px-3.5 py-3">
              <div className="mr-auto">
                <p className="text-caption text-label-3">멈춘 위치</p>
                <p className="font-mono text-headline tabular-nums text-label">{clock(paused)}</p>
              </div>
              {/* 버튼 문구는 명사구로 쓰고, 멈춘 위치를 넣을 입력란을 밝힌다. */}
              <Button size="sm" variant="secondary" onClick={() => setStart(paused.toFixed(1))}>
                시작점으로
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setEnd(paused.toFixed(1))}>
                끝점으로
              </Button>
            </div>
          )
        )}
        {/*
          초 입력란은 숫자 폭으로 둔다. 넓게 채우면 숫자가 입력란 가운데에 떠서 어느 값을 입력하는지 헷갈린다.
          placeholder에 예시 값을 넣으면 이미 들어간 값처럼 읽혀서 비워 둔다.
        */}
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="clip-start">시작</Label>
            <UnitInput
              id="clip-start" unit="초" width="md" step="0.1" min="0" inputMode="decimal"
              value={start}
              invalid={start !== "" && end !== "" && !valid}
              onChange={(e) => setStart(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="clip-end">끝</Label>
            <UnitInput
              id="clip-end" unit="초" width="md" step="0.1" min="0" inputMode="decimal"
              value={end}
              invalid={start !== "" && end !== "" && !valid}
              onChange={(e) => setEnd(e.target.value)}
            />
          </div>
        </div>
        {/* 넣은 값을 `m:ss.s` 형식으로 한 번 더 보여 준다. 초로만 보면 몇 분 몇 초인지 계산해야 한다. */}
        {valid && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-control border border-separator px-3.5 py-2.5">
            <span className="text-caption text-label-3">추가할 구간</span>
            <span className="font-mono text-footnote tabular-nums text-label">
              {clock(Number(start))} – {clock(Number(end))} · {(Number(end) - Number(start)).toFixed(1)}초
            </span>
          </div>
        )}
        {start !== "" && end !== "" && !ordered && (
          <p className="mt-2 text-footnote text-bad-text" role="alert">끝이 시작보다 뒤여야 합니다.</p>
        )}
        {tooShort && (
          <p className="mt-2 text-footnote text-bad-text" role="alert">구간은 {minSeconds}초 이상이어야 합니다.</p>
        )}
      </Modal>
    </div>
  );
}
