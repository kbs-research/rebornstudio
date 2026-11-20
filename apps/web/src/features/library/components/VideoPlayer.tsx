/**
 * Vidstack 기반 영상 플레이어. 워크벤치·검수·클립 뷰어·영상 상세가 함께 쓴다.
 *
 * 클립 구간 재생, 다운로드 차단, 방향키 ±10초 탐색, 재생 주소 갱신을 한 곳에서 맡는다.
 * 위치를 읽을 때는 원본 `<video>`의 절대 시간을, 쓸 때는 Vidstack의 상대 API를 쓴다.
 */
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";

import { useQuery } from "@tanstack/react-query";
import {
  MediaPlayer,
  MediaProvider,
  type MediaPlayerInstance,
  type MediaProviderInstance,
} from "@vidstack/react";
import {
  defaultLayoutIcons,
  DefaultVideoLayout,
} from "@vidstack/react/player/layouts/default";
import { ChevronsLeft, ChevronsRight, Loader2, RotateCw, VideoOff } from "lucide-react";
import { memo, type RefObject, useEffect, useMemo, useRef, useState } from "react";

import { fetchVideo, fetchVideoSource, libraryKeys } from "../api";
import { playerErrorView } from "../lib/player_error";
import { mediaErrorAction, playerKey, resumeSeek, type PlaybackSnapshot } from "../lib/player_source";
import type { ApiError } from "@/shared/api/client";
import { useProject } from "@/features/projects";
import { hasProjectRole, WORK_RESULT_VIEWER_ROLES } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { cn } from "@/shared/lib/cn";

/**
 * ±10초 탐색 피드백 배지를 그린다. 방향키로 탐색하면 왼쪽이나 오른쪽에 잠깐 나타난다.
 *
 * 숨길 때는 `invisible`(visibility)을 함께 써서 접근성 트리와 `innerText`에서도 뺀다. 불투명도 전환은 그대로 적용된다.
 * `opacity-0`만 쓰면 두 배지가 항상 접근성 트리에 남아 보조기기와 텍스트 추출이 "10초 10초"를 읽는다.
 */
function SeekBadge({ dir, active }: { dir: "fwd" | "back"; active: boolean }) {
  const Chevron = dir === "fwd" ? ChevronsRight : ChevronsLeft;
  return (
    <div
      aria-hidden={!active}
      className={cn(
        "flex flex-col items-center gap-0.5 rounded-card bg-black/55 px-5 py-3 text-white transition-[opacity,visibility] duration-300",
        active ? "visible opacity-100" : "invisible opacity-0",
      )}
    >
      <Chevron size={26} aria-hidden="true" />
      <span className="text-micro font-semibold tabular-nums">10초</span>
    </div>
  );
}

/** 방향키 탐색 한 번의 이동량(초, ±10초). */
const ARROW_SEEK_S = 10;

/**
 * 재생기 안의 원본 `<video>`를 찾는다. 위치를 읽을 때 쓴다(읽기는 절대 시간, 쓰기는 상대 API).
 *
 * `<video>`는 마운트와 소스 교체 시점에 따라 나중에 생기므로 저장해 두지 않고 매번 찾는다.
 */
function rawVideoOf(player: { el?: HTMLElement | null }): HTMLVideoElement | null {
  return (player.el?.querySelector("video") as HTMLVideoElement | null) ?? null;
}

/**
 * 클립 끝을 당기는 시간(초). 클립은 half-open 구간 [start, end)이다.
 *
 * 인접 클립은 경계 시각을 공유하고(end[i] == start[i+1]), 경계 시각의 프레임은 다음 클립의 첫 프레임이다.
 * 그래서 `clipEndTime`을 end보다 이 값만큼 당겨 마지막 표시 프레임이 현재 클립 안에 머물게 한다.
 * 한 프레임보다 길어야 경계 프레임이 빠진다. 방송 최저 23.976fps에서 한 프레임은 약 0.042초라 0.05초로 둔다.
 * 현재 클립의 마지막 한 프레임이 표시되지 않지만, 다음 클립의 프레임을 보여 주는 것보다 영향이 작다.
 */
const CLIP_END_TRIM_S = 0.05;

/** 영상 공급자가 붙었는지 다시 확인하는 간격(밀리초). */
const PROVIDER_CHECK_MS = 1000;
/** 영상 공급자를 다시 붙이는 최대 횟수. 넘으면 플레이어 자체의 오류 표시에 맡긴다. */
const PROVIDER_RETRY_MAX = 5;

interface Props {
  videoId: string;
  playerRef?: RefObject<MediaPlayerInstance>;
  /** 클립 구간 시작(초). 끝과 함께 주어지고 end>start일 때만 그 구간으로 자른다. */
  startSeconds?: number;
  endSeconds?: number;
  /**
   * 로드가 끝나면 자동 재생하려면 true. 클립 뷰어가 쓴다. can-play에서 `play()`를 직접 부른다.
   * Vidstack의 `autoPlay` prop은 클립 구간과 충돌해 재생 위치가 끝 시각으로 건너뛰므로 쓰지 않는다.
   */
  autoPlay?: boolean;
  /**
   * 배속 메뉴의 상한. 기본값은 2배다. 클립 뷰어처럼 많은 클립을 빠르게 훑는 화면만 4배로 올린다.
   * 라벨링·검수는 캡션을 따라 읽어야 해서 2배로 둔다.
   */
  maxPlaybackRate?: 2 | 4;
  /**
   * Vidstack 내장 단축키를 끄려면 true. 자체 단축키(`useVideoShortcuts`와 페이지 키)를 쓰는 화면에서 켠다.
   * 내장 키(`j` 10초 뒤로, `k` 재생·일시정지, `l` 10초 앞으로)가 자체 재생 키와 겹치면 한 번 누를 때 두 동작이 실행된다.
   */
  disableBuiltinKeys?: boolean;
  /** 부모가 관리하는 배속. 로드할 때 적용하므로 클립마다 다시 마운트돼도 배속이 유지된다. */
  playbackRate?: number;
  /** 사용자가 바꾼 배속을 부모에 전달하는 콜백. 참조가 렌더마다 바뀌면 이벤트를 다시 달므로 `useCallback`으로 감싼다. */
  onPlaybackRateChange?: (rate: number) => void;
  /**
   * 다시 해도 같은 실패(재생본 없음·권한·없는 영상)를 16:9 영역 대신 낮은 줄로 그리려면 true.
   * 재생이 주 목적이 아닌 영상 상세가 켠다. 검은 영역이 첫 화면을 차지하면 준비 진행·클립이 아래로 밀린다.
   */
  compactError?: boolean;
  /**
   * 컨트롤 줄의 영상 제목을 숨기려면 true. 화면 머리가 이미 제목을 보여 주는 곳이 켠다.
   * 제목은 플레이어의 접근성 이름(`MediaPlayer`의 `title`)으로 남는다.
   */
  hideTitle?: boolean;
  /**
   * 구간 조회가 아직 끝나지 않았으면 true. 그동안 불러오는 중 상태로 둔다.
   * 구간 없이 먼저 그리면 영상 전체를 받기 시작했다가 구간이 오면 다시 받는다.
   */
  rangePending?: boolean;
}

/** 상한별 배속 메뉴 항목. 4배는 훑어보기용이라 촘촘한 저배속 대신 넓은 간격으로 둔다. */
const PLAYBACK_RATES: Record<2 | 4, number[]> = {
  2: [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2],
  4: [0.5, 1, 1.25, 1.5, 2, 2.5, 3, 4],
};

/**
 * Vidstack 영상 플레이어를 그린다. 영상 ID로 재생 주소를 받아 온 뒤 렌더한다.
 *
 * `startSeconds`·`endSeconds`가 있으면 `clipStartTime`·`clipEndTime`으로 그 구간만 재생하고,
 * 끝은 `CLIP_END_TRIM_S`만큼 당겨 다음 클립의 첫 프레임을 뺀다.
 * 학습 데이터 영상이 밖으로 나가지 않게 우클릭과 다운로드를 막는다(`onContextMenu`, `controlsList`).
 */
function VideoPlayerImpl({
  videoId,
  playerRef,
  startSeconds,
  endSeconds,
  autoPlay,
  maxPlaybackRate = 2,
  disableBuiltinKeys,
  playbackRate,
  onPlaybackRateChange,
  compactError = false,
  hideTitle = false,
  rangePending = false,
}: Props) {
  const internalRef = useRef<MediaPlayerInstance>(null);
  const ref = playerRef ?? internalRef;
  const providerRef = useRef<MediaProviderInstance>(null);
  // 방향키 ±10초 탐색 오버레이. null이면 숨긴다.
  const [seekHint, setSeekHint] = useState<"fwd" | "back" | null>(null);

  /*
    두 조회의 실패는 아래 오류 화면이 표시하므로 전역 토스트를 끈다(`meta.handled`).
    토스트까지 띄우면 같은 문구가 두 번 뜨고, 토스트가 머리의 버튼을 몇 초간 가린다.
  */
  const video = useQuery({
    queryKey: libraryKeys.video(videoId),
    queryFn: () => fetchVideo(videoId),
    meta: { handled: true },
  });
  /*
    `originalBlocked`: 원본을 받을 수 없는 역할이면 참. 서버는 비식별화가 필요한 영상의 원본을 작업 결과를 보는 역할(관리자·검수자)에게만 준다.
    그 밖의 사람이 받은 재생본 없음 응답은 재생본을 준비하는 중이라는 뜻이라 오류 화면의 문구가 달라진다(`playerErrorView`).
    프로젝트 조회는 다른 화면과 같은 키라 요청이 늘지 않는다.
  */
  const { user } = useAuth();
  const project = useProject(video.data?.project_id);
  const originalBlocked = !!project.data && !hasProjectRole(user, project.data, WORK_RESULT_VIEWER_ROLES);
  const src = useQuery({
    queryKey: libraryKeys.videoSource(videoId),
    queryFn: () => fetchVideoSource(videoId),
    meta: { handled: true },
    // presigned URL의 TTL은 1시간이다. 50분마다 한 번 새로 받는다.
    refetchInterval: 50 * 60_000,
    // 서명에 초 단위 시각이 들어가 이 요청은 부를 때마다 다른 URL을 돌려주고, 브라우저는 URL이 다르면 다른 파일로 본다.
    // 기본값 `staleTime: 0`이면 마운트·창 포커스마다 URL이 바뀌어 영상을 처음부터 다시 받는다.
    // 그래서 갱신 주기와 같은 값으로 두어 50분 동안 같은 URL을 쓴다.
    staleTime: 50 * 60_000,
    // 기본 gcTime 5분이면 작업을 옮겨 다니는 사이 캐시가 지워져 staleTime이 적용되지 않는다.
    gcTime: 55 * 60_000,
  });

  const hasClip = endSeconds != null && startSeconds != null && endSeconds > startSeconds;
  const clipStart = startSeconds ?? 0;
  const srcUrl = src.data?.url;
  /* 구간마다 재생기를 따로 만든다(`lib/player_source.playerKey`). 재생기에 이벤트를 다는 이펙트도 이 키로 다시 단다.
     구간이 늦게 와서 바뀌면 문장 레인(클립 기준)과 재생 막대(영상 기준)가 어긋나기 때문이다. */
  const instanceKey = playerKey(startSeconds, endSeconds);
  // 소스 객체를 `srcUrl` 기준으로 메모이즈한다. 부모(워크벤치)가 카운트다운으로 매초 리렌더돼도
  // 참조가 같으면 Vidstack이 미디어를 다시 불러오지 않는다.
  const source = useMemo(() => (srcUrl ? { src: srcUrl, type: "video/mp4" as const } : null), [srcUrl]);

  /* 영상 공급자가 붙지 않았으면 다시 붙인다.
     Vidstack은 `<video>`가 생길 때 ref 콜백에서 공급자 로드를 한 번만 부르고, 그 프레임에 내부 준비가 덜 끝났으면 다시 부르지 않는다.
     그러면 주소는 있는데 `<video>`에 붙지 않아 로딩 표시만 계속된다. 개발 모드는 마운트를 두 번 해서 이 증상이 보이지 않고 빌드 번들에서 나타난다.
     공급자가 이미 있으면 아무것도 하지 않는다. 로드 중인 공급자를 끊지 않도록 1초 간격으로만 다시 부른다. */
  useEffect(() => {
    if (!srcUrl) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      const player = ref.current;
      const outlet = providerRef.current;
      if (!player || !outlet) return;
      if (player.provider || tries >= PROVIDER_RETRY_MAX) {
        window.clearInterval(timer);
        return;
      }
      const v = rawVideoOf(player);
      if (!v) return;
      tries += 1;
      outlet.load(v);
    }, PROVIDER_CHECK_MS);
    return () => window.clearInterval(timer);
  }, [ref, srcUrl, instanceKey]);

  /* 재생을 준비한다. 다운로드 차단 속성을 걸고, `autoPlay`이면 정렬, 재생, 위치 검증을 차례로 한다.
     로드 직후 코드가 부르는 `play()`는 Vidstack의 replay 경로로 처리되어 `boundTime(t) = t + clipStart`가 한 번 더 걸리고,
     위치가 클립 시작의 2배가 된다. 그래서 재생 전에 상대 0으로 탐색을 한 번 거쳐 내부 상태를 사용자 조작과 같게 만든다.
     상대 0은 이미 놓인 클립 시작이라 실제 이동도 추가 로딩도 없다.
     순서는 can-play, `currentTime = 0`(정렬), seeked, `play()`이고, 첫 playing에서 위치를 한 번 검증한다. */
  const autoPlayedRef = useRef(false);
  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    const rawVideo = () => rawVideoOf(player);

    const applyGuards = () => {
      // <video>는 마운트·소스 교체 시점에 따라 나중에 생기므로 매번 다시 찾는다.
      const v = rawVideo();
      if (!v) return;
      v.setAttribute("controlsList", "nodownload noremoteplayback");
      v.disablePictureInPicture = true;
    };

    let phase: "idle" | "aligning" | "started" = "idle";
    let verifyPending = true;
    let alignTimer: number | undefined;

    const beginPlay = () => {
      phase = "started";
      // `autoPlayedRef`는 재생에 성공했을 때만 참으로 바꾼다. 실패에서 바꾸면 뒤이은 can-play가
      // 다시 시도하지 못해 그 클립만 멈춘다.
      void player.play?.().then(() => { autoPlayedRef.current = true; }).catch(() => { phase = "idle"; });
    };

    const startAutoPlay = () => {
      if (!autoPlay || autoPlayedRef.current || phase !== "idle") return;
      const d = player.duration;
      if (!Number.isFinite(d) || d <= 0) return; // 메타데이터가 아직 없다. 다음 can-play에서 다시 시도한다.
      if (!hasClip) return beginPlay();
      phase = "aligning";
      player.currentTime = 0;
      // 이동량이 0에 가까워 seeked가 오지 않을 수 있다. 그래서 짧게 기다린 뒤 그대로 재생한다.
      alignTimer = window.setTimeout(() => { if (phase === "aligning") beginPlay(); }, 400);
    };

    /**
     * 시작 위치를 검증한다. 클립 시작을 벗어났으면 상대 API로 되돌린다.
     *
     * 자동 재생의 첫 playing에서 한 번만 한다. seeked·playing마다 검사하면 사용자가 재생 막대로 옮긴 위치까지
     * 되돌려서, 재생 중에 막대를 누르면 클립 처음으로 돌아간다.
     */
    const verifyStart = () => {
      if (!verifyPending || !hasClip || phase !== "started") return;
      const v = rawVideo();
      if (!v || !Number.isFinite(v.duration) || v.duration <= 0) return;
      verifyPending = false; // 판정했으면 끝낸다. 이후 사용자의 탐색에는 개입하지 않는다.
      if (v.currentTime > clipStart + 1) player.currentTime = 0;
    };

    const onCanPlay = () => { applyGuards(); startAutoPlay(); };
    const onSeeked = () => {
      if (phase === "aligning") {
        window.clearTimeout(alignTimer);
        beginPlay();
      }
    };
    applyGuards();
    player.addEventListener("can-play", onCanPlay);
    player.addEventListener("seeked", onSeeked);
    player.addEventListener("playing", verifyStart);
    return () => {
      player.removeEventListener("can-play", onCanPlay);
      player.removeEventListener("seeked", onSeeked);
      player.removeEventListener("playing", verifyStart);
      window.clearTimeout(alignTimer);
    };
  }, [ref, srcUrl, instanceKey, autoPlay, hasClip, clipStart]);

  // 사용자가 바꾼 배속을 부모에 전달한다. 클립마다 재생기를 새로 마운트하는 클립 뷰어에서 배속이 1배로 돌아가지 않게 한다.
  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    // 적용은 `MediaPlayer`의 `playbackRate` prop이 한다(아래 렌더). `player.playbackRate`에 직접 쓰면
    // Vidstack이 can-play 때 prop 값(기본 1배)을 다시 적용해 덮어쓴다.
    // 게터는 재생 준비가 끝난 뒤에만 읽는다. 공급자가 생기기 전에는 예외를 던진다.
    const onRateChange = () => {
      if (player.state.canPlay) onPlaybackRateChange?.(player.playbackRate);
    };
    player.addEventListener("rate-change", onRateChange);
    return () => player.removeEventListener("rate-change", onRateChange);
  }, [ref, srcUrl, instanceKey, onPlaybackRateChange]);

  // 재생을 시작할 때 위치를 바로잡는다. 다음 두 경우에만 클립 처음으로 되감는다.
  //  1) 끝까지 본 뒤 다시 재생한다. 클립 구간에서는 ended·seeking 이벤트 순서가 자주 어긋나서 play 시점의 위치로 판단한다.
  //  2) 위치가 클립 밖에 있다. can-play 직후 잘못된 ended가 뜬 상태에서 `play()`가 replay 경로로 처리되면
  //     `boundTime(t) = t + clipStart` 변환이 한 번 더 걸려 위치가 클립 시작의 2배가 된다.
  // 되감기는 상대 API(`player.currentTime = 0`)로 한다. 원본 `<video>`에 직접 쓰면 Vidstack 내부 상태(ended·replaying)와
  // 어긋나 재생이 시작하는 순간 멈춘다.
  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    const onPlay = () => {
      const d = player.duration;
      if (!d || !Number.isFinite(d)) return;
      // 위치는 원본 `<video>`의 절대 시간으로 읽는다. `player.currentTime`은 클립 매핑이 안정되기 전에 잠깐 어긋나고,
      // replay 경로가 바로 그 순간에 실행되어 끝 위치를 판정하지 못한다.
      const v = rawVideoOf(player);
      if (!v || !Number.isFinite(v.duration) || v.duration <= 0) return;
      const abs = v.currentTime;
      const end = clipStart + d;
      // 끝 근처에 있거나 클립 밖에 있을 때만 되감는다. 중간에서 재생하면 위치를 바꾸지 않는다.
      if (abs >= end - 0.1 || abs < clipStart - 0.1) player.currentTime = 0;
    };
    player.addEventListener("play", onPlay);
    return () => player.removeEventListener("play", onPlay);
  }, [ref, srcUrl, instanceKey]);

  /*
    재생 주소를 새로 받아도 보던 위치를 유지한다.
    주소는 만료 전에 50분마다 바뀌고, 바뀌면 미디어가 처음부터 다시 열린다.
    그래서 재생 위치와 일시정지 상태를 계속 기록해 두었다가, 주소가 바뀐 뒤 첫 can-play에서 그 위치로 돌아간다.
    되돌리는 동안에는 기록하지 않는다. 새 미디어가 여는 0초(클립이면 클립 시작)가 기록한 위치를 덮기 때문이다.
  */
  const lastPlayback = useRef<PlaybackSnapshot | null>(null);
  const prevSrcUrl = useRef<string | undefined>(undefined);
  const restoring = useRef(false);
  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    const record = () => {
      if (restoring.current) return;
      const v = rawVideoOf(player);
      if (v && Number.isFinite(v.currentTime) && v.readyState > 0) {
        lastPlayback.current = { abs: v.currentTime, paused: v.paused };
      }
    };
    player.addEventListener("time-update", record);
    player.addEventListener("pause", record);
    player.addEventListener("play", record);
    // 탐색이 끝나야 time-update가 온다. 만료된 주소로 탐색하면 끝나지 않으므로 시작할 때 목표 위치를 기록한다.
    player.addEventListener("seeking", record);
    return () => {
      player.removeEventListener("time-update", record);
      player.removeEventListener("pause", record);
      player.removeEventListener("play", record);
      player.removeEventListener("seeking", record);
    };
  }, [ref, srcUrl, instanceKey]);
  useEffect(() => {
    const player = ref.current;
    if (!player || !srcUrl) return;
    const target = resumeSeek(prevSrcUrl.current, srcUrl, lastPlayback.current, hasClip ? clipStart : 0);
    prevSrcUrl.current = srcUrl;
    if (!target) return;
    restoring.current = true;
    const onCanPlay = () => {
      player.removeEventListener("can-play", onCanPlay);
      // 위치 쓰기는 상대 API로 한다.
      player.currentTime = target.seek;
      restoring.current = false;
      if (target.resume) void player.play?.().catch(() => {});
    };
    player.addEventListener("can-play", onCanPlay);
    return () => {
      player.removeEventListener("can-play", onCanPlay);
      restoring.current = false;
    };
  }, [ref, srcUrl, hasClip, clipStart]);
  // 구간이 바뀌면 다른 재생기라서 앞 재생기의 위치를 새 구간에 옮기지 않는다.
  useEffect(() => {
    lastPlayback.current = null;
  }, [instanceKey]);

  /* 미디어 오류를 복구한다. 재생 주소가 만료되어 범위 요청이 403 응답을 받으면 재생기는 스피너만 표시한다.
     그래서 주소를 한 번 새로 받아 위의 위치 복원으로 이어 재생하고, 새 주소로도 실패하면 안내를 띄운다.
     판정은 `lib/player_source.mediaErrorAction`이 하고, 재생이 다시 준비되면 횟수를 비운다.
     한 실패에 오류 이벤트가 여러 번 오므로 새 주소를 기다리는 동안 같은 주소의 오류는 세지 않는다.
     처음 여는 요청이 403이면 브라우저가 `<source>`에만 오류를 내고 재생기의 `error`는 오지 않는다.
     그래서 재생기 요소에서 캡처 단계로 `<source>`의 오류도 받는다. */
  const [mediaFailed, setMediaFailed] = useState(false);
  const refreshes = useRef(0);
  const refreshedFrom = useRef<string | null>(null);
  const refetchSource = src.refetch;
  useEffect(() => {
    const player = ref.current;
    if (!player || !srcUrl) return;
    const handle = (code: number | undefined) => {
      if (refreshedFrom.current === srcUrl) return;
      const action = mediaErrorAction(code, refreshes.current);
      if (action === "refresh") {
        refreshes.current += 1;
        refreshedFrom.current = srcUrl;
        // 같은 주소가 다시 오면 재생기가 미디어를 새로 열지 않으므로 기다리지 않고 안내한다.
        void refetchSource().then((r) => {
          if (r.data?.url === srcUrl) setMediaFailed(true);
        });
      } else if (action === "fail") {
        setMediaFailed(true);
      }
    };
    const onError = (e: Event) => handle((e as CustomEvent<{ code?: number }>).detail?.code);
    // 포스터·썸네일 같은 다른 요소의 오류는 거른다. `<source>` 오류는 소스 오류(4)로 본다.
    const onSourceError = (e: Event) => {
      if (e.target instanceof HTMLSourceElement) handle(4);
    };
    const root = player.el;
    const onReady = () => {
      refreshes.current = 0;
      refreshedFrom.current = null;
      setMediaFailed(false);
    };
    player.addEventListener("error", onError);
    player.addEventListener("can-play", onReady);
    root?.addEventListener("error", onSourceError, true);
    return () => {
      player.removeEventListener("error", onError);
      player.removeEventListener("can-play", onReady);
      root?.removeEventListener("error", onSourceError, true);
    };
  }, [ref, srcUrl, instanceKey, refetchSource]);
  /** 안내의 `다시 시도`. 횟수를 비우고 주소를 새로 받는다. */
  const retryMedia = () => {
    refreshes.current = 0;
    refreshedFrom.current = null;
    setMediaFailed(false);
    void refetchSource();
  };

  // 방향키로 ±10초 탐색하고 오버레이를 띄운다. 모든 워크벤치가 함께 쓴다.
  // Vidstack 기본 방향키 탐색이나 워크벤치의 window 핸들러와 겹쳐 두 번 탐색하지 않도록
  // window 캡처 단계에서 처리하고 전파를 끊는다. 입력란(캡션 등)에서는 커서 이동에 쓰이므로 처리하지 않는다.
  useEffect(() => {
    let hideId: number | undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      /*
        보조 키가 같이 눌렸으면 처리하지 않는다. `Alt+←/→`(뒤로·앞으로)와 ⌘/Ctrl 조합은 브라우저·OS 단축키다.
        이 핸들러는 캡처 단계에서 전파를 끊으므로, 보조 키를 거르지 않으면 그 단축키가 10초 탐색으로 바뀐다.
      */
      if (e.altKey || e.metaKey || e.ctrlKey) return;
      const t = e.target as HTMLElement | null;
      if (/input|textarea|select/i.test(t?.tagName ?? "") || t?.isContentEditable) return;
      // 슬라이더에 포커스가 있으면 화살표 키는 슬라이더가 처리한다(ARIA). 재생기의 시간·음량 슬라이더나 화자 타임라인에서
      // 이 핸들러가 키를 먼저 처리하면 슬라이더 대신 영상이 10초씩 건너뛴다.
      if (t?.getAttribute("role") === "slider") return;
      const player = ref.current;
      if (!player) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      // 끝까지 본 뒤 되감으면 이어서 재생한다. 미디어는 끝나면 정지 상태라 탐색만 하면 멈춘 채 남고,
      // 클립을 여러 번 돌려 보는 검수에서 매번 재생을 다시 눌러야 한다.
      const wasEnded = player.state.ended;
      if (e.key === "ArrowRight") {
        player.currentTime = player.currentTime + ARROW_SEEK_S;
        setSeekHint("fwd");
      } else {
        player.currentTime = Math.max(0, player.currentTime - ARROW_SEEK_S);
        setSeekHint("back");
      }
      if (wasEnded) void player.play?.().catch(() => {});
      window.clearTimeout(hideId);
      hideId = window.setTimeout(() => setSeekHint(null), 550);
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => {
      window.removeEventListener("keydown", onKey, { capture: true });
      window.clearTimeout(hideId);
    };
  }, [ref]);

  // `isLoading` 대신 `isPending`으로 판단한다. 재시도 백오프 사이에는 `isFetching`이 잠깐 false라서
  // `isLoading`으로 판단하면 실패가 확정되기 전에 오류 화면이 잠깐 나타난다.
  if (video.isPending || src.isPending || rangePending) {
    return (
      <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-card bg-black/85">
        <Loader2 size={26} className="animate-spin text-white/50" aria-hidden="true" />
        <p className="text-footnote text-white/70">영상 불러오는 중…</p>
      </div>
    );
  }
  if (video.error || src.error || !video.data || !srcUrl) {
    /* 영상 메타·재생 주소 API의 실패를 그린다. 미디어 자체의 재생 실패(주소 만료 등)는 재생기 위의 안내(`mediaFailed`)가 맡는다.
       제목·원인·`다시 시도` 여부는 `lib/player_error.playerErrorView`가 오류 사전(`shared/api/error_text`)으로 고른다.
       이 엔드포인트의 흔한 실패인 409 `video_not_uploaded`·`invalid_source_uri`는 네트워크와 관계가 없어서,
       상태 코드로만 나누면 409에도 네트워크를 의심하라는 안내가 나간다.
       어두운 배경 위 글자는 대비를 맞추려고 흰색 70% 이상으로 쓴다. */
    const err = (video.error ?? src.error) as ApiError | null;
    const view = playerErrorView(err, { originalBlocked });
    if (compactError && !view.retry) {
      return (
        <div role="alert" className="flex items-center gap-3 rounded-card bg-black/85 px-4 py-3.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-capsule bg-white/[0.07]">
            <VideoOff size={16} className="text-white/60" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-footnote font-medium text-white/90">{view.title}</p>
            <p className="text-caption text-white/80">{view.reason}</p>
            {view.hint && <p className="text-caption text-white/70">{view.hint}</p>}
          </div>
        </div>
      );
    }
    return (
      <div
        role="alert"
        className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-card bg-black/85 px-6 text-center"
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-capsule bg-white/[0.07]">
          <VideoOff size={18} className="text-white/60" aria-hidden="true" />
        </div>
        <div className="space-y-1">
          <p className="text-callout font-medium text-white/90">{view.title}</p>
          <p className="text-footnote text-white/80">{view.reason}</p>
          {view.hint && <p className="text-caption text-white/70">{view.hint}</p>}
        </div>
        {view.retry && (
          <button
            type="button"
            onClick={() => { void video.refetch(); void src.refetch(); }}
            className="mt-1 inline-flex items-center gap-1.5 rounded-capsule border border-white/25 px-3 py-1.5 text-footnote text-white/85 transition hover:border-white/40 hover:bg-white/[0.06] hover:text-white"
          >
            <RotateCw size={14} aria-hidden="true" /> 다시 시도
          </button>
        )}
        {view.code != null && <p className="font-mono text-nano text-white/60">HTTP {view.code}</p>}
      </div>
    );
  }

  // 경계 프레임(다음 클립의 첫 프레임)을 빼려고 `clipEndTime`만 `CLIP_END_TRIM_S`만큼 당긴다.
  // `Math.max`는 끝이 시작보다 앞서지 않게 막는다. 실제 클립은 이 값보다 훨씬 길다.
  const clipProps = hasClip
    ? { clipStartTime: clipStart, clipEndTime: Math.max(clipStart, endSeconds! - CLIP_END_TRIM_S) }
    : {};

  return (
    <div className="relative w-full" onContextMenu={(e) => e.preventDefault()}>
      <MediaPlayer
        key={instanceKey}
        ref={ref}
        title={video.data.title}
        src={source!}
        aspectRatio="16/9"
        // 즉시 로드한다. 기본 전략(`visible`)은 문서가 숨겨진 상태(백그라운드 탭·가려진 창)이면
        // 로딩을 시작하지 않아 워크벤치가 검은 화면으로 남는다.
        load="eager"
        // 클립 구간은 수십 초라 통째로 미리 받는다. `metadata`면 구간 안에서 이동할 때마다
        // 원격 저장소에 범위 요청이 나가 탐색과 자동 재생이 느려진다. 전체 영상은 길어서 `metadata`로 둔다.
        preload={hasClip ? "auto" : "metadata"}
        // 부모가 관리하는 배속. 클립마다 다시 마운트돼도 이 prop으로 배속이 유지된다.
        playbackRate={playbackRate ?? 1}
        keyDisabled={disableBuiltinKeys}
        className="overflow-hidden rounded-card bg-black"
        {...clipProps}
      >
        <MediaProvider ref={providerRef} />
        {/* Google Cast 버튼을 뺀다. 학습 데이터 영상은 외부로 송출하지 않는다. */}
        <DefaultVideoLayout
          icons={defaultLayoutIcons}
          slots={hideTitle
            ? { googleCastButton: null, title: null, chapterTitle: null }
            : { googleCastButton: null }}
          playbackRates={PLAYBACK_RATES[maxPlaybackRate]}
        />
      </MediaPlayer>
      {/*
        미디어 오류 안내. 주소를 새로 받아도 재생하지 못한 경우에 조회 실패 화면과 같은 모양으로 재생기 위에 덮는다.
        바탕이 비치면 재생기의 버퍼링 표시와 컨트롤이 안내 뒤로 겹쳐 보여 불투명하게 둔다.
        브라우저가 원인을 전달하지 않아 가능한 원인 두 가지를 쓴다.
      */}
      {mediaFailed && (
        <div
          role="alert"
          className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 rounded-card bg-black px-6 text-center"
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-capsule bg-white/[0.07]">
            <VideoOff size={18} className="text-white/60" aria-hidden="true" />
          </div>
          <div className="space-y-1">
            <p className="text-callout font-medium text-white/90">영상 재생 실패</p>
            <p className="text-footnote text-white/80">재생 주소가 만료되었거나 저장소에 연결하지 못했습니다.</p>
          </div>
          <button
            type="button"
            onClick={retryMedia}
            className="mt-1 inline-flex items-center gap-1.5 rounded-capsule border border-white/25 px-3 py-1.5 text-footnote text-white/85 transition hover:border-white/40 hover:bg-white/[0.06] hover:text-white"
          >
            <RotateCw size={14} aria-hidden="true" /> 다시 시도
          </button>
        </div>
      )}
      {/* 방향키 ±10초 탐색 피드백. 컨트롤 클릭을 막지 않도록 pointer-events-none을 쓴다. */}
      <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-between px-8">
        <SeekBadge dir="back" active={seekHint === "back"} />
        <SeekBadge dir="fwd" active={seekHint === "fwd"} />
      </div>
    </div>
  );
}

/**
 * `memo`로 감싼 영상 플레이어. 워크벤치는 카운트다운으로 매초 리렌더된다.
 * props(영상 ID·ref·구간)가 같으면 리렌더를 건너뛰어 플레이어가 다시 초기화되지 않는다.
 */
export const VideoPlayer = memo(VideoPlayerImpl);
