/**
 * 영상 등록 시트.
 *
 * 등록은 가끔 하는 작업(주 몇 회, 한 번에 수십 건)이라 페이지 안 인라인 패널 대신 오버레이 시트로 연다.
 * 인라인 패널은 목록을 400px 밀어내서 올리는 동안 목록의 첫 행만 보인다.
 *
 * 구조:
 *  1. 메타는 시트에 두고 두 경로(NCP·파일)가 함께 쓴다.
 *     경로마다 설정을 따로 두면 경로가 늘 때마다 한 경로만 설정값을 하드코딩하는 결함이 생긴다.
 *  2. 왼쪽은 대상 입력, 오른쪽은 처리 방법인 두 칸으로 나눈다.
 *     결정에 필요한 값(대상·길이)과 실행 버튼은 사이드바 한 곳에 모은다.
 *     저장소 탭은 버킷 안을 나열하지 않고 경로를 직접 받는다(`StoragePathInput`).
 *
 * 이 시트는 분석 요청과 예상 비용을 두지 않는다. 등록만으로는 과금되지 않고,
 * 분석은 과금되는 결정이라 대상과 예상 비용을 분석 요청 확인 창에서 보고 정한다.
 *
 * 업로드 중에 시트를 닫으면 언마운트하지 않고 숨긴다. 엔진 상태가 유지되어 업로드가 계속되고, 페이지가 진행 줄을 보여 준다.
 */
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { FolderInput, Info, UploadCloud, X } from "lucide-react";

import { StoragePathInput } from "./StoragePathInput";
import { SOURCE_LABEL } from "../lib/vocab";
import { Button } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Select } from "@/shared/ui/Select";
import { cn } from "@/shared/lib/cn";
import { useDialogFocus } from "@/shared/hooks/useDialogFocus";
import { fmtSpan } from "@/shared/lib/fmtSecs";

import {
  type EngineAction, type IngestMeta, UploadQueue,
  type UploadProgress, type UploadSummary,
} from "./UploadQueue";
import { fetchUploadLimits, ingestKeys, renameIngestBatch } from "../api";

type Source = "ncp" | "file";

type Profile = "auto" | "always" | "never";

/**
 * 변환 선택지마다 달라지는 점을 한 문장으로 쓴다. 고른 값의 설명만 보여 준다.
 * 내용은 서버 판정(`media.transcode.plan_outputs`)과 같다.
 */
const PROFILE_HINT: Record<Profile, string> = {
  auto: "H.264가 아니거나 720p를 넘는 영상만 변환합니다.",
  always: "모든 영상의 분석본과 재생본을 만듭니다.",
  never: "변환하지 않고 원본을 그대로 분석과 재생에 씁니다.",
};

/**
 * 엔진이 보고한 실행 사양 중 화면에 그리는 부분.
 * run은 ref로 따로 보관한다(아래 `runRefs` 참조).
 */
type ActionMeta = Pick<EngineAction, "count" | "label" | "busy">;
const NO_ACTION: ActionMeta = { count: 0, label: "", busy: false };

interface Props {
  projectId: string;
  open: boolean;
  /** 시트를 닫는다. 업로드 중이면 숨기기만 하고 엔진은 계속 돈다. */
  onClose: () => void;
  /**
   * 등록 완료를 알린다.
   * 1건이면 페이지가 그 영상 상세로, 여러 건이면 `?batch=` Job 상세로 이동한다.
   * `note`는 엔진이 결과 토스트에 덧붙일 안내다(이미 등록됐던 파일 수 등). 토스트는 페이지가 하나만 띄운다.
   */
  onDone: (batchId: string, count: number, firstVideoId?: string, note?: string) => void;
  /**
   * 폴더째 등록 작업이 큐에 들어갔음을 알린다.
   * 등록은 서버가 하므로 이 시점에는 건수와 id가 없다. 진행은 Job 행이 표시한다.
   */
  onJobQueued: (batchId: string) => void;
  /** 진행 중인 업로드 수를 알린다. 페이지의 접힌 진행 줄이 쓴다. */
  onBusyChange: (n: number) => void;
  /**
   * 업로드 진행 요약을 알린다. 접힌 진행 줄이 `N / M건 · P%`를 표시한다.
   * 시트 실행부도 같은 값을 그리므로 시트를 접었다 펼쳐도 수가 달라지지 않는다.
   */
  onProgress?: (p: UploadProgress | null) => void;
}

/** 영상 등록 시트를 그린다. 저장소 경로 입력과 파일 업로드 엔진을 탭으로 담는다. */
export function IngestSheet({
  projectId, open, onClose, onDone, onJobQueued, onBusyChange, onProgress,
}: Props) {

  const [source, setSource] = useState<Source>("ncp"); // 대량 등록이 들어오는 NCP 탭을 먼저 연다.
  const [jobLabel, setJobLabel] = useState("");
  /**
   * 변환 프로파일. 기본값은 자동이다.
   * 등록 시점에는 코덱·해상도 정보가 없고, probe가 판정한다.
   */
  const [profile, setProfile] = useState<Profile>("auto");
  const [fileSummary, setFileSummary] = useState<UploadSummary>({ ready: 0, durationKnown: 0, durationUnknown: 0 });
  /** 실행부가 그리는 전체 진행. 페이지의 접힌 줄과 같은 값이다. */
  const [fileProgress, setFileProgress] = useState<UploadProgress | null>(null);
  const [actions, setActions] = useState<Record<Source, ActionMeta>>({ ncp: NO_ACTION, file: NO_ACTION });
  /** 엔진별 최신 run 클로저. 상태로 보관하면 매 렌더 새 함수가 되어 렌더가 무한히 반복된다. */
  const runRefs = useRef<Record<Source, () => void>>({ ncp: () => {}, file: () => {} });

  /*
    엔진이 보고한 실행 사양을 받는다.
    두 엔진은 탭을 바꿔도 선택이 남도록 항상 마운트되어 있어서, 엔진별로 슬롯을 나눠 받는다.
    엔진은 매 렌더 보고한다. 값이 같으면 같은 객체를 돌려주어 렌더 반복을 막는다.
  */
  const report = useMemo(() => {
    const make = (key: Source) => (a: EngineAction) => {
      runRefs.current[key] = a.run;
      setActions((cur) => {
        const p = cur[key];
        return p.count === a.count && p.label === a.label && p.busy === a.busy
          ? cur
          : { ...cur, [key]: { count: a.count, label: a.label, busy: a.busy } };
      });
    };
    return { ncp: make("ncp"), file: make("file") };
  }, []);

  /**
   * 업로드 조건을 시트가 열릴 때 한 번 조회한다.
   *
   * 크기 상한과 받는 확장자는 파일마다 바뀌지 않는다. 미리 받지 않으면 첫 파일이 멀티파트를 시작해야
   * 상한을 알 수 있어 큰 파일이 영상 레코드를 만든 뒤에야 거부된다.
   * 조회가 실패해도 엔진은 예비 목록으로 계속 돌고 등록을 막지 않는다.
   */
  const limits = useQuery({
    queryKey: ingestKeys.uploadLimits(projectId),
    queryFn: () => fetchUploadLimits(projectId),
    enabled: open && !!projectId,
    staleTime: 60 * 60_000,
    retry: false,
  });

  /**
   * 진행 보고를 시트의 실행부와 페이지의 접힌 줄 두 곳에 전달한다.
   *
   * 주의: 엔진은 매 렌더 보고하므로 이 함수의 참조가 바뀌면 렌더가 반복된다.
   * 부모 콜백은 ref로 보관하고, 값이 같으면 상태를 바꾸지 않는다(위 `report`와 같은 이유).
   */
  const onProgressRef = useRef(onProgress);
  onProgressRef.current = onProgress;
  const handleProgress = useCallback((p: UploadProgress | null) => {
    setFileProgress((cur) =>
      cur?.done === p?.done && cur?.total === p?.total && cur?.ratio === p?.ratio ? cur : p);
    onProgressRef.current?.(p);
  }, []);

  /** 시트 패널. 열려 있는 동안 포커스를 이 안에 가둔다(`Modal`과 같은 `useDialogFocus`). */
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus(open, panelRef);

  // ESC로 시트를 닫는다(업로드 중이면 숨긴다). 시트가 열려 있을 때만 키 입력을 받는다.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  /**
   * 등록에 필요한 메타.
   *
   * 과금은 사람이 누르는 '분석 요청' 버튼에서 일어나므로 등록은 막히지 않고 메타는 항상 채워진다.
   */
  const meta: IngestMeta = {
    // 방송사·프로그램명은 등록에서 받지 않는다. 빈 값을 보내 서버 기본값을 쓴다.
    broadcaster: "",
    program_name: "",
    transcode_profile: profile,
  };

  /**
   * 두 엔진이 함께 쓰는 등록 완료 처리.
   *
   * 등록은 분석을 요청하지 않는다. 분석은 비용이 드는 결정이라 사용자가 대상과 비용을 보고 Job 상세에서 직접 요청한다.
   */
  const handleDone = (batchId: string, count: number, videoIds: string[], note?: string) => {
    /*
      Job 제목은 등록이 끝난 뒤 붙인다.
      서버는 첫 영상이 들어올 때 배치 행을 만든다(ensure_batch). 그 전에 PATCH하면 404 응답을 받는다.
      제목 저장이 실패해도 등록은 성공했으므로 알리지 않는다. 이름은 나중에 목록에서 달 수 있다.
    */
    if (jobLabel.trim()) {
      void renameIngestBatch(batchId, jobLabel.trim()).catch(() => undefined);
    }
    // 1건 등록이면 영상 상세로 바로 가도록 첫 영상 id를 넘긴다. 이동 규칙은 콘솔이 정한다.
    onDone(batchId, count, videoIds[0], note);
  };

  const action = actions[source];
  /** 실행할 수 있는지 판정한다. 이 엔진에 처리할 대상이 있고 진행 중이 아니어야 한다. */
  const canRun = action.count > 0 && !action.busy;
  /**
   * 버튼을 누를 수 없는 이유. 버튼을 보는 위치에서 읽히도록 버튼 바로 아래에 쓴다.
   * 방향(`왼쪽에`)은 좁은 폭에서 틀리므로 쓰지 않는다.
   */
  const blockedReason = action.count === 0
      ? source === "ncp"
        ? "등록할 파일 경로를 입력하세요."
        : "등록할 파일을 추가하세요."
      : null;

  return (
    // 업로드 상태를 유지하려고 언마운트하지 않고 숨긴다. hidden이면 오버레이도 이벤트도 없다.
    <div
      className={cn("fixed inset-0 z-50 items-center justify-center bg-black/40 p-3 sm:p-6", open ? "flex animate-fade-in" : "hidden")}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* 업로드 큐와 긴 경로 입력을 담는 창이라 넓게 연다.
          좁은 모달에 넣으면 긴 파일 이름과 키가 잘린다. */}
      {/* 숨긴 동안에는 대화상자 역할을 떼어 낸다. 숨은 시트가 `role="dialog"`로 남으면
          `[role="dialog"]`를 찾는 코드가 같은 화면에 열린 확인 창 대신 이 시트를 먼저 찾는다. */}
      <div
        ref={panelRef}
        tabIndex={-1}
        role={open ? "dialog" : undefined}
        aria-modal={open ? true : undefined}
        aria-label={open ? "영상 등록" : undefined}
        className="flex h-[min(88vh,54rem)] w-full max-w-[76rem] animate-pop-in flex-col overflow-hidden rounded-card border border-separator bg-raised shadow-e3 focus:outline-none"
      >
        <header className="flex shrink-0 items-center gap-2.5 border-b border-separator px-5 py-3.5">
          <UploadCloud size={16} className="text-label-3" aria-hidden="true" />
          <h2 className="text-headline font-semibold text-label">영상 등록</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            title="닫아도 업로드는 계속 진행됩니다"
            className="ml-auto rounded-control p-1.5 text-label-4 transition-colors hover:bg-fill hover:text-label-2"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        {/* 좁은 화면에서는 세로로 쌓고 바깥 영역이 스크롤한다.
            두 영역이 각자 스크롤하면 바깥 영역으로 스크롤이 이어지지 않는다. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
          {/* ── 왼쪽: 등록 대상 고르기 ── */}
          <div className="flex min-w-0 flex-1 flex-col gap-4 p-5 lg:overflow-y-auto">
            {/* 출처 탭. 저장소(버킷) 탭을 앞에 둔다. 대량 등록은 드래그로 올리지 않는다. */}
            <div className="flex items-center gap-1 border-b border-separator" role="tablist" aria-label="등록 경로">
              {(
                [
                  /* 탭 이름은 목록·출처 필터와 같은 이름(`SOURCE_LABEL`)을 쓴다.
                     탭 둘이 같은 축(출처)으로 읽혀야 나란히 비교된다. */
                  { key: "ncp", label: SOURCE_LABEL.ncp, Icon: FolderInput },
                  { key: "file", label: SOURCE_LABEL.upload, Icon: UploadCloud },
                ] as const
              ).map(({ key, label, Icon }) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={source === key}
                  onClick={() => setSource(key)}
                  className={cn(
                    "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-callout transition-colors",
                    source === key
                      ? "border-accent font-medium text-label"
                      : "border-transparent text-label-3 hover:text-label-2",
                  )}
                >
                  <Icon size={14} aria-hidden="true" /> {label}
                </button>
              ))}
            </div>

            {/* 엔진은 숨김으로 전환한다.
                조건부 렌더링으로 바꾸면 탭을 오갈 때 입력과 업로드가 사라진다. */}
            <div className={cn(source !== "ncp" && "hidden")}>
              <StoragePathInput
                meta={meta}
                videoExtensions={limits.data?.video_extensions}
                onDone={handleDone}
                onJobQueued={onJobQueued}
                onActionChange={report.ncp}
              />
            </div>
            <div className={cn(source !== "file" && "hidden")}>
              <UploadQueue
                projectId={projectId}
                meta={meta}
                onDone={handleDone}
                limits={limits.data}
                onBusyChange={onBusyChange}
                onProgress={handleProgress}
                onSummary={setFileSummary}
                onActionChange={report.file}
              />
            </div>
          </div>

          {/* ── 오른쪽: 등록 설정과 실행 ──
              스크롤되는 입력부와 바닥에 붙는 실행부로 나눈다. 실행부는 누르기 직전에 확인할 정보(대상·길이·진행)만 담는다.
              모든 요소를 같은 간격으로 나열하면 묶음의 경계가 보이지 않는다. */}
          <aside className="flex w-full shrink-0 flex-col border-t border-separator bg-canvas-2 lg:w-[21rem] lg:border-l lg:border-t-0">
            <div className="flex-1 space-y-5 overflow-y-auto p-5">
              {/*
                등록만으로는 과금되지 않는다는 사실을 입력보다 먼저 알린다.
                채운 상자 대신 왼쪽 세로선을 쓴다. 입력보다 앞에 오는 글에 면이 있으면 시선이 그 글에서 멈춘다.
              */}
              {/* text-pretty: 좁은 영역에서 마지막 줄에 한 어절만 남는 것을 브라우저가 피한다. */}
              <p className="flex gap-2 text-pretty border-l-2 border-accent/40 pl-2.5 text-caption leading-relaxed text-label-3">
                <Info size={14} className="mt-0.5 shrink-0 text-accent/70" aria-hidden="true" />
                {/* 문장 하나에 내용 하나만 쓴다. 버튼 이름을 인용하지 않는다. */}
                <span>등록에는 분석 비용이 들지 않습니다.</span>
              </p>

              {/*
                Job 제목은 이번 등록 1회분의 이름이다. 비우면 등록 시각과 출처로 이름을 만든다.
                제목이 있어야 나중에 어떤 등록이었는지 이름으로 찾을 수 있다.
              */}
              <Field
                label="묶음 이름"
                hint="비워 두면 등록 시각과 출처로 이름을 정합니다."
                htmlFor="in-joblabel"
              >
                <Input id="in-joblabel" value={jobLabel} placeholder="예: 2월 4주 저녁 뉴스 등록"
                  onChange={(e) => setJobLabel(e.target.value)} />
              </Field>

              {/* 방송사·프로그램명은 등록에서 받지 않는다. 등록 시점에 입력하면 등록 1회분 전체에 같은 값이 들어가
                  폴더째 등록에서 서로 다른 프로그램이 한 값으로 합쳐진다. 두 값은 영상 단위로 넣는다.
                  Job 제목은 이번 등록 1회분의 이름이라 그대로 받는다. */}

              {/* 조건은 라벨 괄호 대신 아래 도움말에 쓴다.
                  라벨이 길면 입력란이 뒤로 밀린다. */}
              <Field
                label="변환"
                hint={PROFILE_HINT[profile]}
                htmlFor="in-transcode"
              >
                <Select
                  id="in-transcode"
                  value={profile}
                  onChange={(e) => setProfile(e.target.value as Profile)}
                >
                  {/* 옵션 옆에 괄호 설명을 붙이지 않는다. 조건은 아래 도움말에 쓴다. */}
                  <option value="auto">자동 · 권장</option>
                  <option value="always">항상 변환</option>
                  <option value="never">변환 없음</option>
                </Select>
              </Field>
            </div>

            {/* ── 실행부: 바닥에 붙는다. 누르기 직전에 확인할 정보만 담는다. ── */}
            <div className="shrink-0 space-y-3 border-t border-separator bg-panel p-5">
              {/*
                대상 요약. 버튼이 처리할 대상을 그대로 세고, 값이 있는 항목만 표시한다.
                대상 수와 총 길이는 비교하는 값이라 가로로 나란히 둔다. 우측 정렬 dt/dd는 값이 길면 줄이 깨진다.
              */}
              <dl className="grid grid-cols-2 gap-2 text-center">
                <Stat label="대상" value={action.count > 0 ? `${action.count.toLocaleString("ko-KR")}개` : "—"} strong />
                <Stat
                  label="총 길이"
                  value={source === "file" && fileSummary.durationKnown > 0
                    ? fmtSpan(fileSummary.durationKnown) : "—"}
                />
              </dl>

              {/*
                전체 진행 한 줄. 페이지의 접힌 줄과 같은 값을 써서 시트를 접었다 펼쳐도 수가 달라지지 않는다.
                분모는 이번 실행 대상이다.
              */}
              {source === "file" && fileProgress && (
                <div className="space-y-1">
                  <p className="flex items-baseline gap-1.5 text-caption text-label-3">
                    <span>전체 진행</span>
                    <b className="tabular-nums text-footnote text-label">
                      {Math.round(fileProgress.ratio * 100)}%
                    </b>
                    <span className="ml-auto tabular-nums text-label-3">
                      {fileProgress.done.toLocaleString("ko-KR")} / {fileProgress.total.toLocaleString("ko-KR")}개
                    </span>
                  </p>
                  <div className="h-1.5 overflow-hidden rounded-capsule bg-fill">
                    {/* 전이 시간은 200ms다.
                        진행 이벤트가 초당 수십 번 오므로 전이가 느리면 막대가 항상 실제보다 뒤처져 보인다. */}
                    <div
                      className="h-full rounded-capsule bg-accent transition-[width] duration-200"
                      style={{ width: `${Math.round(fileProgress.ratio * 100)}%` }}
                    />
                  </div>
                </div>
              )}

              {/* 값이 '—'인 이유를 밝힌다. 빈 값에 이유가 없으면 고장 난 것으로 보인다. */}
              {source === "ncp" ? (
                <p className="text-caption leading-snug text-label-3">
                  저장소 등록은 길이를 미리 알 수 없습니다. 등록 후 규격 검증에서 확인됩니다.
                </p>
              ) : fileSummary.durationUnknown > 0 ? (
                /* 총 길이에 분모를 붙인다. 업로드가 멈춘 파일은 길이 정보가 없다. */
                <p className="text-caption leading-snug tabular-nums text-label-3">
                  총 길이는 길이를 아는 {Math.max(0, fileSummary.ready - fileSummary.durationUnknown).toLocaleString("ko-KR")}개 기준입니다.
                </p>
              ) : null}

              <div className="space-y-1.5">
                <Button
                  className="w-full justify-center"
                  disabled={!canRun}
                  onClick={() => runRefs.current[source]()}
                >
                  {action.busy ? "진행 중…" : action.count > 0 ? action.label : "등록"}
                </Button>
                {blockedReason && (
                  <p className="text-center text-caption leading-snug text-label-3">{blockedReason}</p>
                )}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

/** 라벨, 입력란, 도움말을 한 묶음으로 그린다. 조건과 단서는 라벨 괄호 대신 아래에 둔다. */
function Field({
  label, hint, htmlFor, children,
}: {
  label: string;
  hint?: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="mt-1 text-caption leading-snug text-label-3">{hint}</p>}
    </div>
  );
}

/** 실행부의 값 하나를 그린다. 라벨이 위, 값이 아래다. 값이 없으면 '—'로 표시해 위치를 유지한다. */
function Stat({
  label, value, icon, strong, tone,
}: {
  label: string;
  value: string;
  icon?: ReactNode;
  strong?: boolean;
  tone?: "warn";
}) {
  const unknown = value === "—";
  return (
    <div className="rounded-control bg-canvas-2 px-1.5 py-1.5">
      <dt className="flex items-center justify-center gap-1 text-caption text-label-3">
        {icon}{label}
      </dt>
      <dd className={cn(
        "mt-0.5 tabular-nums",
        unknown ? "text-label-3"
          : tone === "warn" ? "font-mono font-semibold text-warn-text"
          : strong ? "text-footnote font-semibold text-label" : "text-footnote text-label-2",
      )}>
        {value}
      </dd>
    </div>
  );
}
