/**
 * 분석 대상 카드(설정 `분석 연동`). 이 프로젝트의 영상을 분석할 연결·워크스페이스·분석 프로젝트를 고르고 점검한다.
 *
 * 분석 방식이 분석 서비스를 쓰는 프로젝트는 이 값을 저장해야 영상을 등록하고 분석을 요청한다.
 * 미리 채워 두는 값은 없다. 자격증명 연결에 등록한 분석 연결 중에서 고른다.
 * 고를 연결이 없으면 자격증명 연결 화면으로 가는 길을 같은 카드에 둔다. 첫 영상이 등록되면 바꿀 수 없다.
 * 고르는 규칙·잠금·점검은 서버(`services/stacks/project_stack`)가 정하고 화면은 그 결과를 그린다.
 * 바꿀 수 없는 이유와 판단 근거(영상 수, 점검 결과)는 같은 카드에 둔다.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Lock, TriangleAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Select } from "@/shared/ui/Select";
import { SettingCard, SettingRow } from "@/shared/ui/SettingRow";
import { Skeleton } from "@/shared/ui/Skeleton";
import { useToast } from "@/shared/ui/Toast";
import { failToast } from "@/shared/ui/failToast";
import { cn } from "@/shared/lib/cn";
import { DiagnosticsList, diagnosticsSummary } from "@/features/platform";
import type { DiagnosticsReport } from "@/shared/api/client";

import {
  checkAnalysisStack,
  fetchAnalysisChoices,
  projectKeys,
  saveAnalysisStack,
  type AnalysisChoicesDTO,
} from "../api";
import {
  NO_STREAK, TRANSIENT_RETRY_MS, advanceStreak, lockTitle, projectOptionLabel, transientRetryDelay,
  type TransientStreak,
} from "../lib/stack_target";
import { NoConnectionNotice } from "./NoConnectionNotice";
import { TargetNotice as Notice } from "./TargetNotice";

/** 고르는 값 셋. 빈 문자열은 아직 고르지 않은 값이다. */
interface TargetForm {
  connectionId: string;
  workspace: string;
  projectId: string;
}

const EMPTY: TargetForm = { connectionId: "", workspace: "", projectId: "" };

/** 응답이 일시 오류(요청량 제한 등)면 참. 분류는 서버(`error_transient`)가 정한다. */
function isTransient(d: AnalysisChoicesDTO | undefined): boolean {
  return !!(d?.error && d.error_transient);
}

/**
 * 일시 오류가 이어지는 동안 상한까지 스스로 다시 부르는 `refetchInterval`을 만든다.
 * 연속 수는 조회마다 따로 센다. 화면 문구도 같은 수를 읽어 자동 재조회가 남았는지 표시한다.
 */
function retryWhileTransient(streak: { current: TransientStreak }) {
  return (q: { state: { data?: AnalysisChoicesDTO; dataUpdatedAt: number } }) => {
    streak.current = advanceStreak(streak.current, isTransient(q.state.data), q.state.dataUpdatedAt);
    return transientRetryDelay(streak.current);
  };
}

/** 서버 선택 값을 폼 값으로 바꾼다. 연결이나 분석 프로젝트가 비었으면 기록이 없다고 본다. */
function formOf(v: AnalysisChoicesDTO["selected"] | null | undefined): TargetForm | null {
  if (!v?.maiu_project_id || !v.analysis_connection_id) return null;
  return {
    connectionId: v.analysis_connection_id,
    workspace: v.maiu_workspace ?? "",
    projectId: v.maiu_project_id,
  };
}

/** 두 폼 값이 같으면 참. */
function sameForm(a: TargetForm, b: TargetForm | null): boolean {
  return !!b && a.connectionId === b.connectionId && a.workspace === b.workspace && a.projectId === b.projectId;
}

/** 연결 선택지 한 줄의 이름. 고를 수 없는 연결은 이유를 붙인다. */
function connectionLabel(c: NonNullable<AnalysisChoicesDTO["connections"]>[number]): string {
  const base = c.name;
  if (c.selectable) return base;
  if (!c.has_secret) return `${base} · 자격증명 없음`;
  return c.last_status === "fail" ? `${base} · 점검 실패` : `${base} · 점검 전`;
}

/**
 * 분석 대상 카드를 그린다.
 * `canManageConnections`는 고를 연결이 없을 때의 안내가 쓴다(`NoConnectionNotice`).
 */
export function AnalysisTargetCard({
  projectId, canManageConnections,
}: {
  projectId: string; canManageConnections: boolean;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<TargetForm>(EMPTY);
  const [ready, setReady] = useState(false);
  const [report, setReport] = useState<DiagnosticsReport | null>(null);

  /*
    두 조회 모두 일시 오류를 받으면 간격을 두고 상한까지 스스로 다시 부른다.
    안내가 "다시 불러옵니다"라고 표시하므로 실제로 다시 불러야 한다.
  */
  const baseStreak = useRef<TransientStreak>(NO_STREAK);
  const browseStreak = useRef<TransientStreak>(NO_STREAK);
  /*
    연결·워크스페이스마다 마지막으로 읽은 분석 프로젝트 목록. 요청량 제한처럼 목록을 잠깐 읽지 못하는 동안
    선택지가 비어 "id 101"만 남지 않게 이 목록을 그대로 보여 준다.
  */
  const lastProjects = useRef(new Map<string, NonNullable<AnalysisChoicesDTO["projects"]>>());
  /* 저장된 선택·잠금·연결 목록은 연결·워크스페이스를 고르지 않은 조회로 받는다. 폼의 처음 값을 정한다. */
  const base = useQuery({
    queryKey: projectKeys.analysisChoices(projectId),
    queryFn: () => fetchAnalysisChoices(projectId),
    refetchInterval: retryWhileTransient(baseStreak),
  });
  /* 고른 연결·워크스페이스의 목록. 고를 때마다 다시 받고 받는 동안 이전 목록을 둔다. */
  const browse = useQuery({
    queryKey: projectKeys.analysisChoices(projectId, form.connectionId, form.workspace),
    queryFn: () => fetchAnalysisChoices(projectId, form.connectionId, form.workspace || null),
    enabled: ready && !!base.data?.supported && !!form.connectionId,
    placeholderData: keepPreviousData,
    refetchInterval: retryWhileTransient(browseStreak),
  });

  const saved = formOf(base.data?.selected);
  /* 처음 한 번만 채운다. 저장된 선택이 없으면 비워 둔다. 채워 줄 기본값은 없다. */
  useEffect(() => {
    if (ready || !base.data) return;
    setForm(formOf(base.data.selected) ?? EMPTY);
    setReady(true);
  }, [base.data, ready]);
  /* 연동 저장소 비교. 고른 워크스페이스의 목록을 받는 동안에는 이전 값이라 판정하지 않는다. */
  const workspaceBucket = (form.connectionId ? browse.data : base.data)?.workspace_bucket ?? null;
  const projectBucket = base.data?.project_bucket ?? null;
  const bucketOff = !!(form.workspace && workspaceBucket && projectBucket && workspaceBucket !== projectBucket);

  const save = useMutation({
    mutationFn: () =>
      saveAnalysisStack(projectId, {
        analysis_connection_id: form.connectionId,
        maiu_workspace: form.workspace || null,
        maiu_project_id: form.projectId || null,
      }),
    onSuccess: () => {
      toast("success", "분석 대상을 저장했습니다", "이제 영상을 등록할 수 있습니다.");
      setReport(null);
      void qc.invalidateQueries({ queryKey: projectKeys.analysisChoicesAll(projectId) });
      void qc.invalidateQueries({ queryKey: projectKeys.analysisProvisioning(projectId) });
      void qc.invalidateQueries({ queryKey: projectKeys.list() });
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
    },
    onError: (e) => failToast(toast, e, "저장 실패"),
  });
  const check = useMutation({
    mutationFn: () => checkAnalysisStack(projectId),
    onSuccess: setReport,
    onError: (e) => failToast(toast, e, "점검 실패"),
  });

  if (base.isLoading) {
    return (
      <Card as="section" aria-label="분석 대상" className="space-y-3 px-5 py-4">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </Card>
    );
  }
  if (!base.data) {
    return (
      <Card as="section" aria-label="분석 대상" className="px-5 py-4">
        <ErrorState inline message="분석 대상 조회 실패" onRetry={() => void base.refetch()} />
      </Card>
    );
  }

  const data = base.data;
  if (!data.supported) {
    return (
      <Card as="section" aria-label="분석 대상" className="px-5 py-4">
        <h3 className="text-callout font-semibold text-label">분석 대상</h3>
        <p className="mt-0.5 text-footnote text-label-2">이 프로젝트의 분석 방식은 분석 서비스를 쓰지 않습니다.</p>
      </Card>
    );
  }

  const list = (form.connectionId ? browse.data : null) ?? data;
  /* 안내가 읽는 연속 수는 지금 그리는 목록을 받은 조회에서 센 수다. 같은 응답은 다시 세지 않는다. */
  const [streak, from] = form.connectionId && browse.data ? [browseStreak, browse] : [baseStreak, base];
  streak.current = advanceStreak(streak.current, isTransient(list), from.dataUpdatedAt);
  const autoRetry = transientRetryDelay(streak.current) !== false;
  const locked = data.locked;
  const complete = !!(form.connectionId && form.workspace && form.projectId);
  const dirty = !sameForm(form, saved);
  const conns = data.connections ?? [];
  const workspaces = list.workspaces ?? [];
  const browseKey = `${form.connectionId}|${form.workspace}`;
  if (browse.data?.projects && !browse.isPlaceholderData) lastProjects.current.set(browseKey, browse.data.projects);
  const projects = form.workspace ? (list.projects ?? lastProjects.current.get(browseKey) ?? []) : [];
  /* 저장된 분석 프로젝트의 이름. 서버가 저장하거나 목록을 읽을 때 확인한 이름이라 목록을 읽지 못해도 있다. */
  const savedName = form.projectId === saved?.projectId
    ? (list.selected_project_name ?? data.selected_project_name)
    : null;

  function pick(key: keyof TargetForm, value: string) {
    setReport(null);
    setForm((f) => {
      if (key === "connectionId") return { connectionId: value, workspace: "", projectId: "" };
      if (key === "workspace") return { ...f, workspace: value, projectId: "" };
      return { ...f, projectId: value };
    });
  }

  return (
    <SettingCard aria-label="분석 대상">
      <div className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            <h3 className="text-callout font-semibold text-label">분석 대상</h3>
            <p className="mt-0.5 text-footnote text-label-2">이 프로젝트의 영상을 보낼 연결·워크스페이스·분석 프로젝트입니다.</p>
          </div>
          <span
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 text-footnote font-semibold",
              locked ? "text-label-2" : saved ? "text-ok-text" : "text-warn-text",
            )}
          >
            {locked ? <Lock size={13} aria-hidden="true" /> : (
              <span aria-hidden="true" className={cn("size-2 rounded-capsule", saved ? "bg-ok" : "bg-warn")} />
            )}
            {locked ? "고정됨" : saved ? "저장됨" : "저장 전"}
          </span>
        </div>

        {/* 잠긴 이유와 저장 전에 막힌 것을 선택 목록 위에 둔다. 결과(굵게)와 이유(작게)를 나눈다. */}
        {locked && !saved && data.required ? (
          <Notice tone="warn" title="영상이 있는데 분석 대상이 기록되지 않았습니다">
            기존 영상이 쓴 분석 연결을 최고관리자가 기록해야 영상 등록과 분석 요청이 풀립니다.
          </Notice>
        ) : locked ? (
          <Notice tone="neutral" title={lockTitle(data.video_count, data.deleted_video_count, "분석 대상")}>
            한 프로젝트의 분석 결과가 두 분석 프로젝트에 나뉘지 않게 첫 영상 등록부터 고정합니다.
          </Notice>
        ) : !conns.length && data.required ? (
          <NoConnectionNotice kind="analysis" canManage={canManageConnections} />
        ) : !saved && data.required ? (
          <Notice tone="warn" title="분석 대상을 저장하기 전에는 영상을 등록할 수 없습니다">
            연결·워크스페이스·분석 프로젝트를 고른 뒤 저장합니다.
          </Notice>
        ) : null}
      </div>

      <SettingRow
        label="분석 연결"
        htmlFor="analysis-target-connection"
        description="자격증명 연결에 등록한 분석 연결입니다."
        control={
          <div className="w-72 shrink-0">
            <Select
              id="analysis-target-connection"
              value={form.connectionId}
              disabled={locked}
              onChange={(e) => pick("connectionId", e.target.value)}
            >
              <option value="" disabled>{conns.length ? "고르기" : "등록한 연결 없음"}</option>
              {conns.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.selectable && c.id !== form.connectionId}>
                  {connectionLabel(c)}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      <SettingRow
        label="워크스페이스"
        htmlFor="analysis-target-workspace"
        description="워크스페이스에 연결된 저장소가 이 프로젝트의 저장소와 같아야 합니다."
        control={
          <div className="w-72 shrink-0">
            <Select
              id="analysis-target-workspace"
              value={form.workspace}
              disabled={locked || !form.connectionId}
              onChange={(e) => pick("workspace", e.target.value)}
            >
              <option value="">
                {!form.connectionId ? "연결을 먼저 고르기"
                  : browse.isFetching && !workspaces.length ? "불러오는 중…" : "고르기"}
              </option>
              {workspaces.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.bucket && data.project_bucket && w.bucket !== data.project_bucket
                    ? `${w.name} · 저장소 다름`
                    : w.name}
                </option>
              ))}
              {form.workspace && !workspaces.some((w) => w.name === form.workspace) && (
                <option value={form.workspace}>{form.workspace}</option>
              )}
            </Select>
          </div>
        }
      />
      <SettingRow
        label="분석 프로젝트"
        htmlFor="analysis-target-project"
        description="영상이 등록될 분석 서비스 안의 프로젝트입니다."
        control={
          <div className="w-72 shrink-0">
            <Select
              id="analysis-target-project"
              value={form.projectId}
              disabled={locked || !form.workspace}
              onChange={(e) => pick("projectId", e.target.value)}
            >
              <option value="">{form.workspace ? "고르기" : "워크스페이스를 먼저 고르기"}</option>
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {projectOptionLabel(p.name, p.project_id)}
                  {p.provisioned ? " · 이 프로젝트용으로 만든 프로젝트" : p.is_base_project ? " · 기본" : ""}
                </option>
              ))}
              {form.projectId && !projects.some((p) => p.project_id === form.projectId) && (
                <option value={form.projectId}>{projectOptionLabel(savedName, form.projectId)}</option>
              )}
            </Select>
          </div>
        }
      />

      <div className="px-5 py-4">
        {/* 요청량 제한 같은 일시 오류는 실패와 구분해 표시한다. 분류는 서버(`error_transient`)가 점검과 같은 기준으로 정한다. */}
        {list.error && list.error_transient && (
          <div role="status" className="mb-3 flex flex-wrap items-start gap-x-3 gap-y-2 text-footnote">
            <TriangleAlert size={14} className="mt-0.5 shrink-0 text-warn-text" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-warn-text">{list.error}</p>
              <p className="mt-0.5 text-label-2">
                {autoRetry
                  ? `일시적인 오류입니다. ${TRANSIENT_RETRY_MS / 1000}초 뒤 다시 불러옵니다.`
                  : "일시적인 오류가 이어져 자동으로 다시 불러오지 않습니다."}
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void qc.invalidateQueries({ queryKey: projectKeys.analysisChoicesAll(projectId) })}
              disabled={base.isFetching || browse.isFetching}
            >
              {base.isFetching || browse.isFetching ? "불러오는 중…" : "다시 불러오기"}
            </Button>
          </div>
        )}
        {((list.error && !list.error_transient) || bucketOff) && (
          <p role="status" className="mb-3 flex gap-1.5 text-footnote text-bad-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            {(!list.error_transient && list.error) || `이 워크스페이스에 연결된 저장소는 ${list.workspace_bucket}이고 이 프로젝트는 ${data.project_bucket}을 씁니다.`}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="min-w-0 flex-1 text-footnote text-label-2">
            {locked ? "영상 등록과 분석 요청도 같은 점검을 거칩니다." : "저장하면 접근 권한과 저장소를 실제로 점검한 뒤 기록합니다."}
          </p>
          <Button size="sm" variant="secondary" onClick={() => check.mutate()} disabled={check.isPending}>
            {check.isPending ? "점검 중…" : "점검"}
          </Button>
          {!locked && (
            <Button
              size="sm"
              onClick={() => save.mutate()}
              disabled={save.isPending || !complete || bucketOff || !dirty}
            >
              {save.isPending ? "저장 중…" : "저장"}
            </Button>
          )}
        </div>
      </div>

      {report && (
        <div className="py-3">
          <h4 className="px-5 text-footnote font-semibold text-label">점검 결과 · {diagnosticsSummary(report)}</h4>
          <DiagnosticsList report={report} />
        </div>
      )}
    </SettingCard>
  );
}
