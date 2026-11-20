/**
 * 프로젝트 설정 화면(`/p/:slug/settings`). 프로젝트 관리자만 연다.
 *
 * 왼쪽 메뉴에서 설정 페이지를 고르고 오른쪽에서 값을 고친다.
 * 메뉴는 프로젝트 · 작업 운영 · 영상 처리 묶음으로 나누고, 위험 영역은 따로 둔다.
 * 위험 영역은 삭제 권한이 있는 최고관리자에게만 보인다. 누를 것이 없는 절을 열게 하지 않는다.
 * 기능을 켜고 끄는 항목은 스위치(`shared/ui/Switch`)로 한다.
 * 절 머리에는 설명 대신 적용 시점 같은 사실 하나만 둔다.
 * 페이지와 필드의 대응, 필드 이름·단위·설명은 `lib/settings_form.ts`에 있다.
 *
 * 폼 하나를 모든 페이지가 나눠 쓴다. 저장은 서버 값(`base`)과 다른 필드만 보낸다.
 * 모든 값을 보내면 다른 관리자가 그사이 바꾼 값을 덮는다.
 * 저장하지 않은 변경이 있으면 화면 아래 저장 막대가 무엇이 바뀌었는지 보여 준다.
 * 저장하지 않고 화면을 떠나면 확인 창을 띄운다. 그 창에서 저장하고 나갈 수도 있다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode, type TextareaHTMLAttributes } from "react";
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowRight, Info, TriangleAlert } from "lucide-react";

import { AccessDenied } from "@/shell/AccessDenied";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { DatePicker } from "@/shared/ui/DatePicker";
import { FIELD_BASE } from "@/shared/ui/field";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { useAnalysisStack } from "@/hooks/useAnalysisStack";
import { Modal } from "@/shared/ui/Modal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import { Select } from "@/shared/ui/Select";
import { SettingCard, SettingRow } from "@/shared/ui/SettingRow";
import { Switch } from "@/shared/ui/Switch";
import { useToast } from "@/shared/ui/Toast";
import { failToast } from "@/shared/ui/failToast";
import { UnitInput } from "@/shared/ui/UnitInput";
import { DiagnosticsList, diagnosticsSummary } from "@/features/platform";
import { useUnsavedGuard } from "@/shared/hooks/useUnsavedGuard";
import { type ApiError } from "@/shared/api/client";
import { CAPTION_LENGTH_UNIT } from "@/shared/lib/caption_length";
import { cn } from "@/shared/lib/cn";
import { fmtDate } from "@/shared/lib/time";
import {
  DANGER_PAGE, EMPTY_FORM, FIELD_META, PAGE_GROUPS, changeRows, changeSummary, lineItemCount,
  pageFromParam, patchBody,
  type ChangeRow, type FormKey, type PageKey, type SettingsForm, type SettingsPage,
} from "../lib/settings_form";
import { NUMBER_SPECS, boundsText, checkNumber, type NumberField, type NumberSpec } from "../lib/settings_numbers";
import { useAuth } from "@/store/auth";

import {
  deleteProject,
  fetchAnalysisChoices,
  fetchAnalysisProvisioning,
  fetchConnectionChoices,
  fetchProjectStats,
  projectKeys,
  updateProject,
  useProjects,
  type ProjectUpdateRequest,
} from "../api";
import { AnalysisStackCard } from "../components/AnalysisStackCard";
import { AnalysisTargetCard } from "../components/AnalysisTargetCard";
import { StorageTargetCard } from "../components/StorageTargetCard";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { PROJECT_STATUS_LABEL, PROJECT_STATUS_ORDER } from "../lib/project_status";

/** 422 필드 오류. `api()`가 Pydantic 오류를 `fields: [{path, message}]`로 바꿔 던진다. */
type FieldApiError = ApiError & { fields?: { path: string; message: string }[] };

/**
 * 서버 422의 필드 경로를 폼 필드와 그 옆에 보일 문구로 바꾸는 표. 경로의 끝부분으로 맞춘다(`body.name` 등).
 * 문구는 허용 범위를 쓴다. 서버의 영문 원문은 화면에 보이지 않는다.
 */
const FIELD_OF_PATH: [string, string, string][] = [
  ["name", "name", "1~100자로 입력해주세요."],
  ["contract_deadline", "contract_deadline", "오늘이나 그 뒤의 날짜를 골라주세요."],
  ...(Object.keys(NUMBER_SPECS) as NumberField[]).map((k) => [k, k, boundsText(NUMBER_SPECS[k])] as [string, string, string]),
];

/** 프로젝트 설정 화면. 프로젝트 관리자가 아니면 권한 안내를 보여 준다. */
export function ProjectSettingsPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();

  /*
    현재 페이지는 주소(`?tab=`)에 둔다. 새로고침해도 같은 페이지가 열리고 링크로 건넬 수 있다.
    표에 없는 값이면 기본 정보를 연다. 이전 탭 이름은 `pageFromParam`이 새 페이지로 옮긴다.
  */
  const [params, setParams] = useSearchParams();
  const requestedTab = pageFromParam(params.get("tab"));
  const setTab = (k: PageKey) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (k === "general") next.delete("tab"); else next.set("tab", k);
    return next;
  }, { replace: true });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  // 계약 마감일 입력란에 반영되지 않은 날짜가 있으면 저장을 막는다. 그대로 저장하면 입력한 날짜가 버려진다.
  const [deadlineProblem, setDeadlineProblem] = useState<string | null>(null);
  // 필드 옆 오류 문구. 서버 422가 가리킨 필드와 저장 전 검사에서 걸린 필드에 붙는다.
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  // 서버는 프로젝트 삭제를 최고관리자에게만 허용한다(`soft_delete_project`). 삭제 버튼도 최고관리자에게만 보인다.
  const globalRole = useAuth((st) => st.user?.global_role);
  const canDelete = globalRole === "super_admin";
  // 위험 영역은 삭제 권한이 있을 때만 있다. 권한 없이 주소(`?tab=danger`)로 열면 기본 정보를 연다.
  const tab: PageKey = requestedTab === "danger" && !canDelete ? "general" : requestedTab;

  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  // 이 프로젝트의 분석 방식. 이름·구성·벤더 사용 여부를 이 값 하나에서 읽는다.
  const stack = useAnalysisStack(project);

  /* 고를 수 있는 연결 이름. 연결 이름을 직접 입력하지 않고 이 목록에서 고르게 한다.
     직접 입력하면 오타가 났을 때 그 기능이 알림 없이 실패한다.
     연결 관리 목록은 최고관리자 전용이라 프로젝트 관리자용 이름 목록을 받는다.
     품질검수 모델 연결은 서버가 품질검수에 쓸 수 있는 것만 준다(`purpose=caption_qc`).
     저장소·MI 연결은 모델을 부를 수 없어 저장도 거절된다. 화면이 서비스 이름으로 다시 거르지 않는다. */
  const qcConns = useQuery({
    queryKey: projectKeys.connectionChoices(project?.project_id, "caption_qc"),
    queryFn: () => fetchConnectionChoices(project!.project_id, "caption_qc"),
    enabled: !!project?.project_id,
  });

  const [form, setForm] = useState<SettingsForm>(EMPTY_FORM);

  /* 고른 검사 방식으로 부를 수 있는 연결만 보여 준다. 어떤 방식이 어떤 연결을 받는지는 서버가 연결마다 싣는다(`adapter_keys`). */
  const modelConns = (qcConns.data ?? []).filter((c) => (c.adapter_keys ?? []).includes(form.qc_adapter_key));
  const pickedConn = (qcConns.data ?? []).find((c) => c.name === form.qc_connection_name);

  /*
    서버 값(`base`). 폼과 비교해 바뀐 필드만 보낸다.
    프로젝트 목록을 다시 받으면(창에 다시 포커스가 올 때 포함) 고치지 않은 필드만 새 값으로 바꾸고,
    고친 필드는 그대로 둔다. 폼 전체를 덮으면 입력하던 값이 사라진다.
  */
  // 어느 프로젝트의 서버 값인지도 함께 기억한다. 주소만 바뀌면(다른 프로젝트의 설정으로 이동) 이 화면은
  // 다시 마운트되지 않으므로, 앞 프로젝트에서 고치던 값이 다음 프로젝트로 넘어가지 않게 한다.
  const baseRef = useRef<{ id: string; form: typeof form } | null>(null);
  const [base, setBase] = useState<typeof form | null>(null);
  useEffect(() => {
    if (project) {
      const num = (v: number | null | undefined) => (v != null ? String(v) : "");
      const next: SettingsForm = {
        name: project.name,
        description: project.description ?? "",
        guide_md: project.guide_md ?? "",
        target_hours: num(project.target_hours),
        contract_deadline: project.contract_deadline ?? "",
        project_status: project.status ?? "active",
        deident_enabled: project.deident_enabled,
        qc_enabled: project.caption_qc?.enabled ?? false,
        qc_adapter_key: project.caption_qc?.adapter_key ?? "llm",
        qc_connection_name: project.caption_qc?.connection_name ?? "",
      };
      const prev = baseRef.current?.id === project.project_id ? baseRef.current.form : null;
      baseRef.current = { id: project.project_id, form: next };
      setBase(next);
      setForm((cur) => {
        if (!prev) return next;
        const merged = { ...next };
        for (const k of Object.keys(next) as (keyof typeof next)[]) {
          // 사용자가 고친 필드(지난 서버 값과 다른 필드)는 새로 받은 값으로 덮지 않는다.
          if (cur[k] !== prev[k]) (merged as Record<string, unknown>)[k] = cur[k];
        }
        return merged;
      });
    }
  }, [project]);

  const dirtyKeys = base
    ? (Object.keys(form) as (keyof typeof form)[]).filter((k) => form[k] !== base[k])
    : [];
  const dirty = new Set<FormKey>(dirtyKeys);
  const isDirty = dirtyKeys.length > 0;
  const pageDirty = (p: PageKey) => dirtyKeys.some((k) => FIELD_META[k].page === p);
  // 저장 막대와 나가기 확인창에 보일 변경 목록.
  const changeItems: ChangeRow[] = base ? changeRows(dirtyKeys, base, form) : [];
  const changes = changeItems.map((r) => (r.values ? `${r.label} ${r.values.from} → ${r.values.to}` : r.label));

  /*
    저장하지 않고 화면을 떠나면 확인 창을 띄운다. 설정 페이지끼리는 폼 하나를 나눠 쓰므로 이동해도 값이 남는다.
    브라우저 닫기·새로고침은 `useUnsavedGuard`가, 앱 안의 이동은 라우터 blocker가 막는다.
    프로젝트를 삭제한 직후의 이동은 막지 않는다(`leavingRef`).
  */
  const leavingRef = useRef(false);
  useUnsavedGuard(() => isDirty && !leavingRef.current);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty && !leavingRef.current && currentLocation.pathname !== nextLocation.pathname,
  );

  /**
   * 저장 전에 검사한다. 계약과 같은 규격(`NUMBER_SPECS`)으로 먼저 검사해 필드 옆에 표시한다.
   * 최종 판단은 서버가 한다. 고친 필드만 검사한다. 고치지 않은 값은 보내지 않는다.
   */
  function rangeErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const key of Object.keys(NUMBER_SPECS) as NumberField[]) {
      if (!dirty.has(key)) continue;
      const msg = checkNumber(form[key], NUMBER_SPECS[key]);
      if (msg) out[key] = msg;
    }
    return out;
  }

  const save = useMutation({
    mutationFn: (body: ProjectUpdateRequest) => updateProject(project!.project_id, body),
    // 저장 결과는 앱의 다른 화면처럼 토스트로 알린다.
    onSuccess: () => {
      toast("success", "설정을 저장했습니다");
      setFieldErrors({});
      // 다시 받은 서버 값으로 폼 전체를 맞춘다. 서버가 정리한 값(연결 이름 공백 등)을 받아야
      // 저장한 필드가 저장하지 않은 변경으로 남지 않는다.
      baseRef.current = null;
      qc.invalidateQueries({ queryKey: projectKeys.list() });
    },
    /*
      서버 검증(422)이 가리킨 필드 옆에 허용 범위를 표시한다.
      필드를 가리키지 않는 실패는 사유를 토스트로 알린다. 한 줄로 합치면 어느 필드가 문제인지 알 수 없다.
    */
    onError: (e: FieldApiError) => {
      const byKey: Record<string, string> = {};
      for (const f of e?.fields ?? []) {
        const hit = FIELD_OF_PATH.find(([path]) => f.path === path || f.path.endsWith(`.${path}`));
        if (hit) byKey[hit[1]] = hit[2];
      }
      setFieldErrors(byKey);
      if (Object.keys(byKey).length) toast("error", "저장 실패", "표시한 항목의 값을 확인해주세요.");
      else failToast(toast, e, "저장 실패");
    },
  });

  /**
   * 저장 전 검사를 통과하면 바뀐 필드를 저장한다.
   *
   * Args:
   *   onSaved: 저장에 성공한 뒤 부를 함수. 나가기 확인 창의 `저장하고 나가기`가 이동을 잇는 데 쓴다.
   *   onFailed: 검사나 저장이 실패했을 때 부를 함수. 나가기 확인 창은 이동을 취소해 오류 표시를 보여 준다.
   */
  function submit(onSaved?: () => void, onFailed?: () => void) {
    const errs = rangeErrors();
    setFieldErrors(errs);
    if (Object.keys(errs).length) {
      toast("error", "저장 실패", "표시한 항목의 값을 확인해주세요.");
      onFailed?.();
      return;
    }
    save.mutate(patchBody(form, dirty), { onSuccess: () => onSaved?.(), onError: () => onFailed?.() });
  }

  // 조회 실패와 프로젝트 없음을 구분해 보여 준다.
  if (projects.isLoading) return <PageSkeleton panels={2} />;
  if (!project)
    return projects.isError ? (
      <ErrorState message="프로젝트 조회 실패" description="잠시 후 다시 시도해주세요." onRetry={() => void projects.refetch()} />
    ) : (
      <EmptyState message="프로젝트 없음" description="주소를 확인해주세요. 참여하지 않은 프로젝트는 열 수 없습니다." />
    );
  // 라우터 가드(`RoleGuard`)와 같은 두 줄로 쓴다. 한 줄만 넘기면 누구에게 요청하는지가 빠진다.
  if (project.my_role !== "project_admin")
    return (
      <AccessDenied
        message="프로젝트 관리자만 볼 수 있는 화면입니다."
        hint="권한이 필요하면 프로젝트 관리자에게 요청해주세요."
        project={project}
      />
    );

  /**
   * 폼 필드 하나를 바꾼다. 입력란·날짜·스위치·라디오가 모두 이 함수를 거친다.
   * 거치지 않는 입력이 있으면 그 필드의 변경 표시가 갱신되지 않는다. 고친 필드의 오류 표시는 지운다.
   */
  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((st) => ({ ...st, [key]: value }));
    setFieldErrors((fe) => {
      if (!(key in fe)) return fe;
      const rest = { ...fe };
      delete rest[key as string];
      return rest;
    });
  }
  /** 입력 요소의 `onChange`에 넘길 함수. 값을 문자열로 받아 `set`을 부른다. */
  function field<K extends keyof typeof form>(key: K) {
    return (e: { target: { value: string } }) => set(key, e.target.value as (typeof form)[K]);
  }
  /** 필드 아래의 오류 문구. 서버나 저장 전 검사가 가리킨 필드에만 보인다. */
  const fieldError = (key: string) =>
    fieldErrors[key] ? <p role="alert" className="mt-2 text-footnote text-bad-text">{fieldErrors[key]}</p> : null;

  /** 저장하지 않은 변경을 모두 버리고 서버 값으로 돌린다. */
  function revert() {
    if (base) setForm(base);
    setFieldErrors({});
  }

  /**
   * 설정 페이지의 머리. 절 이름 옆에 이 절에서만 알 수 있는 사실(적용 시점 등) 하나를 둔다.
   * 절을 풀어 설명하는 부제는 두지 않는다. 아래 항목 이름이 그 내용을 보여 준다.
   */
  const pageHead = (title: string, fact?: string) => (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-separator pb-3">
      <h2 className="text-headline text-label">{title}</h2>
      {fact && <p className="text-footnote text-label-2">{fact}</p>}
    </div>
  );
  /** 한 절 안의 묶음 머리. 여러 항목에 같이 해당하는 사실을 항목마다 되풀이하지 않고 묶음 머리에 한 번 쓴다. */
  const groupHead = (title: string, fact?: ReactNode) => (
    <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 px-1">
      <h3 className="text-callout font-semibold text-label">{title}</h3>
      {fact && <p className="text-footnote text-label-2">{fact}</p>}
    </div>
  );
  /** 저장하지 않은 변경이 있는 항목의 이름 옆에 붙는 표시. 지금 봐야 할 표시라 호박색이다. */
  const changed = (...keys: FormKey[]) =>
    keys.some((k) => dirty.has(k)) ? <Chip tone="amber">변경됨</Chip> : null;
  /**
   * 숫자 설정 한 줄. 이름·설명·단위는 `FIELD_META`, 범위는 `NUMBER_SPECS`에서 가져온다.
   * 묶음 안에서 이름을 줄여 쓰면(`라벨러`) 입력란의 접근성 이름은 전체 이름으로 둔다.
   */
  const numberRow = (key: NumberField, opts: { label?: string; hint?: ReactNode } = {}) => {
    const spec: NumberSpec = NUMBER_SPECS[key];
    const label = opts.label ?? FIELD_META[key].label;
    return (
      <SettingRow
        key={key}
        label={label}
        htmlFor={key}
        badge={changed(key)}
        description={opts.hint ?? FIELD_META[key].hint}
        control={
          <UnitInput
            id={key}
            aria-label={label !== FIELD_META[key].label ? FIELD_META[key].label : undefined}
            unit={FIELD_META[key].unit ?? ""}
            min={spec.min}
            max={spec.max ?? undefined}
            step={spec.int ? 1 : "any"}
            value={form[key]}
            onChange={field(key)}
            invalid={!!fieldErrors[key]}
          />
        }
      >
        {fieldError(key)}
      </SettingRow>
    );
  };
  /** 왼쪽 메뉴의 항목 하나. 지금 절은 흰 면으로 띄우고, 저장하지 않은 변경이 있는 절에는 호박색 점을 붙인다. */
  const navItem = (p: SettingsPage) => {
    const active = tab === p.key;
    return (
      <button
        key={p.key}
        type="button"
        onClick={() => setTab(p.key)}
        aria-current={active ? "page" : undefined}
        className={cn(
          "inline-flex shrink-0 items-center gap-2 rounded-control px-3 py-2 text-callout transition-colors",
          active
            ? "bg-panel font-semibold text-label shadow-e1"
            // 위험 영역은 고르지 않았을 때 위험색 글자로 둔다.
            : p.key === "danger" ? "text-bad-text hover:bg-bad-muted" : "text-label-2 hover:bg-fill-2",
        )}
      >
        <span className="flex-1 text-left">{p.label}</span>
        {pageDirty(p.key) && (
          <>
            <span aria-hidden="true" className="size-1.5 shrink-0 rounded-capsule bg-amber" />
            <span className="sr-only"> · 저장하지 않은 변경 있음</span>
          </>
        )}
      </button>
    );
  };
  // 저장 막대에서 저장을 막는 이유. 막는 이유가 있으면 변경 목록 대신 이 문장을 보여 준다.
  const blockedReason = !form.name.trim()
    ? "이름을 입력해야 저장할 수 있습니다."
    : deadlineProblem
      ? "계약 마감일을 다시 확인해야 저장할 수 있습니다."
      : null;

  /** 나가기 확인 창의 `저장하고 나가기`를 처리한다. 저장에 성공하면 이동을 잇고, 실패하면 이동을 취소해 오류 표시를 보여 준다. */
  function saveAndLeave() {
    submit(
      () => {
        leavingRef.current = true;
        blocker.proceed?.();
      },
      () => blocker.reset?.(),
    );
  }

  return (
    <div>
      <PageHeader title="프로젝트 설정" />

      <div className="flex flex-col gap-8 lg:flex-row lg:items-start lg:gap-10">
        {/* 설정 메뉴. 넓은 화면에서는 묶음 이름 아래 세로로, 좁은 화면에서는 가로로 늘어놓는다. */}
        <nav
          aria-label="설정 메뉴"
          className="flex shrink-0 gap-1 overflow-x-auto lg:w-52 lg:flex-col lg:gap-5 lg:overflow-visible"
        >
          {PAGE_GROUPS.map((g) => (
            <div key={g.label} className="flex shrink-0 gap-1 lg:flex-col lg:gap-0.5">
              <div className="hidden px-3 pb-1.5 text-caption font-semibold text-label-3 lg:block">{g.label}</div>
              {g.pages.map(navItem)}
            </div>
          ))}
          {canDelete && (
            <>
              <div aria-hidden="true" className="hidden h-px bg-separator lg:mx-3 lg:block" />
              {navItem(DANGER_PAGE)}
            </>
          )}
        </nav>

        {/* 본문 폭은 폼 폭 720px로 둔다. */}
        <div className="min-w-0 flex-1 space-y-6 lg:max-w-[720px]">
          {/* 설정 페이지: 기본 정보 */}
          {tab === "general" && (
            <>
              {pageHead("기본 정보")}
              <SettingCard aria-label="기본 정보">
                <SettingRow label="이름" htmlFor="name" badge={changed("name")} control={
                  <div className="w-80 min-w-0 shrink">
                    <Input id="name" value={form.name} onChange={field("name")} required aria-invalid={!!fieldErrors.name || undefined} />
                  </div>
                }>
                  {fieldError("name")}
                </SettingRow>
                <SettingRow label="설명" htmlFor="description" badge={changed("description")} stacked control={
                  <textarea
                    id="description"
                    value={form.description}
                    onChange={field("description")}
                    rows={2}
                    className={cn(FIELD_BASE, "resize-y px-3 py-2")}
                  />
                } />
                {/*
                  무엇이 멈추고 무엇이 그대로인지 함께 쓴다. 판정은 서버의 `reject_if_not_claimable`이 한다.
                  삭제 권한이 없는 역할에게는 위험 영역 대신 누가 삭제하는지 한 줄을 둔다.
                */}
                <SettingRow
                  label="상태"
                  labelId="project-status-label"
                  badge={changed("project_status")}
                  description={
                    <>
                      <p>활성이 아니면 새 작업 배정이 멈춥니다. 보유 중인 작업은 이어서 제출할 수 있습니다.</p>
                      {!canDelete && <p className="mt-1">프로젝트 삭제는 최고관리자가 합니다.</p>}
                    </>
                  }
                  control={
                    <SegmentedControl
                      ariaLabel="상태"
                      value={form.project_status}
                      onChange={(v) => set("project_status", v)}
                      options={PROJECT_STATUS_ORDER.map((k) => ({ value: k, label: PROJECT_STATUS_LABEL[k] }))}
                    />
                  }
                />
              </SettingCard>

              {/* 마감일과 목표 분량은 둘 다 통계의 진척 표시에 쓰인다. 그 사실을 입력란마다 쓰지 않고 묶음 머리에 한 번 쓴다. */}
              {groupHead("일정과 목표", "통계의 진척 표시에 둘 다 쓰입니다")}
              <SettingCard aria-label="일정과 목표">
                <SettingRow
                  label="계약 마감일"
                  htmlFor="contract_deadline"
                  badge={changed("contract_deadline")}
                  control={
                    <div className="w-48 shrink-0">
                      <DatePicker
                        id="contract_deadline"
                        value={form.contract_deadline}
                        onChange={(v) => set("contract_deadline", v)}
                        onProblem={setDeadlineProblem}
                        placeholder="마감일 선택"
                      />
                      {fieldError("contract_deadline")}
                    </div>
                  }
                />
                {numberRow("target_hours")}
              </SettingCard>

              <AnalysisStackCard stack={stack} />

              <dl className="flex flex-wrap gap-x-6 gap-y-1 px-1 text-footnote text-label-2">
                <div className="flex gap-2">
                  <dt>주소 식별자</dt>
                  <dd className="font-mono text-label">{project.slug}</dd>
                </div>
                <div className="flex gap-2">
                  <dt>만든 날</dt>
                  {/* 마감일 입력과 같은 `2026년 8월 18일` 꼴로 쓴다. 로케일 기본(`2026. 8. 18.`)은 이 화면의 다른 날짜와 모양이 다르다. */}
                  <dd className="text-label">
                    {fmtDate(project.created_at)}
                  </dd>
                </div>
              </dl>
            </>
          )}

          {/* 설정 페이지: 작업 가이드 */}
          {tab === "guide" && (
            <>
              {pageHead("작업 가이드", "작업자에게 입력한 그대로 보입니다")}
              <Card className="px-5 py-4">
                <div className="flex flex-wrap items-center gap-x-2">
                  {/* 미리보기와 개요 화면 모두 평문으로 보여 준다. 마크다운을 지원한다고 쓰지 않는다. */}
                  <Label htmlFor="guide_md">가이드</Label>
                  {changed("guide_md")}
                </div>
                <CountedTextarea
                  id="guide_md"
                  value={form.guide_md}
                  onChange={field("guide_md")}
                  rows={8}
                  count={`${lineItemCount(form.guide_md)}줄`}
                  placeholder="예: 캡션은 한국어로 씁니다. 화면에 보이는 사실만 씁니다. 인물 이름은 자막을 따릅니다."
                  className="mt-2"
                />
                <p className="mt-2 text-footnote text-label-2">줄바꿈은 그대로 두고, 마크다운 기호는 서식 없이 글자로 보입니다.</p>
              </Card>
              {/*
                미리보기는 작업자가 가장 자주 보는 곳인 작업 화면의 가이드 서랍과 같은 흑연 면으로 그린다.
                서랍과 개요 카드가 같은 원문을 줄바꿈 그대로 보여 주므로 두 곳을 함께 적는다.
              */}
              {groupHead("미리보기", "작업 화면의 가이드 서랍 · 개요의 작업 가이드")}
              <Card data-surface="graphite" className="overflow-hidden bg-canvas-2 text-label">
                <p className="border-b border-separator px-5 py-3 text-headline font-bold text-label">작업 가이드</p>
                <div className="min-h-16 whitespace-pre-line px-5 py-4 text-body text-label">
                  {form.guide_md.trim() || <span className="font-semibold text-label-2">작업 가이드 없음</span>}
                </div>
              </Card>
              {/*
                캡션 최소 길이와 반려 사유는 모든 프로젝트가 같은 값을 쓴다. 고칠 곳이 없어 값만 알린다.
                길이는 글자 수가 아니므로 세는 방법을 함께 쓴다(`CAPTION_LENGTH_UNIT`).
              */}
              <InfoNote title={`캡션 최소 길이 ${project.caption_min_tokens}와 반려 사유는 모든 프로젝트에 같은 기준을 씁니다.`}>
                {CAPTION_LENGTH_UNIT} 작업 화면의 가이드 서랍에 가이드와 함께 보입니다.
              </InfoNote>
            </>
          )}

          {/* 설정 페이지: 캡션 품질검수 */}
          {tab === "qc" && (
            <>
              {pageHead("캡션 품질검수", "걸린 작업은 보류함으로, 반려는 사람이 정합니다")}
              {/* 제출 직후와 승인 직후에 한 번씩 실행된다. 끈 프로젝트에서는 아무것도 하지 않는다. */}
              <SettingCard aria-label="캡션 품질검수 사용">
                <SettingRow
                  label="캡션 품질검수"
                  labelId="qc-enabled-label"
                  badge={changed("qc_enabled")}
                  description="제출 직후와 승인 직후에 캡션을 검사합니다."
                  control={
                    <Switch
                      checked={form.qc_enabled}
                      onChange={(on) => set("qc_enabled", on)}
                      labelledBy="qc-enabled-label"
                    />
                  }
                >
                  {/* 검사에 걸린 작업을 처리하는 화면으로 연결한다. 설명 문장 끝에 붙이지 않고 줄을 뗀다. */}
                  <Link
                    to={`/p/${slug}/qc`}
                    className="mt-2 inline-flex items-center gap-1 text-footnote font-medium text-label hover:underline"
                  >
                    보류 작업
                    <ArrowRight size={12} aria-hidden="true" />
                  </Link>
                </SettingRow>
              </SettingCard>
              {form.qc_enabled && (
                <SettingCard aria-label="검사 설정">
                  <SettingRow
                    label="검사 방식"
                    htmlFor="qc_adapter_key"
                    badge={changed("qc_adapter_key")}
                    description="빈 값·개인정보·비속어·길이 규칙 검사는 방식과 관계없이 항상 실행합니다."
                    control={
                      <div className="w-64 shrink-0">
                        <Select id="qc_adapter_key" value={form.qc_adapter_key} onChange={field("qc_adapter_key")}>
                          <option value="llm">OpenAI 호환·Anthropic 모델</option>
                          <option value="bedrock">AWS Bedrock 모델</option>
                          <option value="dummy">규칙만</option>
                        </Select>
                      </div>
                    }
                  />
                  {/*
                    연결 이름은 목록에서 고르게 한다. 직접 입력하면 오타가 났을 때 규칙 검사만 알림 없이 실행된다.
                    연결은 최고관리자가 등록한다. 프로젝트 관리자는 자격증명 연결 화면을 열 수 없어 링크를 달지 않는다.
                  */}
                  <SettingRow
                    label="모델 연결"
                    htmlFor="qc_connection_name"
                    badge={changed("qc_connection_name")}
                    description={
                      qcConns.isError
                        ? "연결 목록을 불러오지 못했습니다. 저장된 값은 그대로 둡니다."
                        : pickedConn?.last_status === "fail"
                          /* 점검 상태는 연결 점검(15분 주기·사람의 테스트)이 남긴 값이다. 고른 위치에서 바로 알린다. */
                          ? "마지막 점검이 실패한 연결입니다. 고치기 전까지 AI 검사는 실패하고 규칙 검사 결과로만 판정합니다."
                          : "비워 두면 규칙 검사만 실행합니다. 연결은 최고관리자가 자격증명 연결에 등록합니다."
                    }
                    control={
                      <div className="w-64 shrink-0">
                        <Select id="qc_connection_name" value={form.qc_connection_name} onChange={field("qc_connection_name")}>
                          <option value="">선택 안 함</option>
                          {/* 골라도 모델을 부르지 못하는 연결은 그 사실을 이름 옆에 적는다. */}
                          {modelConns.map((c) => (
                            <option key={c.name} value={c.name}>
                              {c.last_status === "fail" ? `${c.name} · 점검 실패` : c.has_secret ? c.name : `${c.name} · 자격증명 없음`}
                            </option>
                          ))}
                          {/* 저장된 이름이 목록에 없어도 그대로 보여 준다. 알리지 않고 비우면 설정이 사라진 것으로 보인다. */}
                          {form.qc_connection_name
                            && !modelConns.some((c) => c.name === form.qc_connection_name) && (
                            <option value={form.qc_connection_name}>
                              {form.qc_connection_name} · {pickedConn ? "검사 방식과 다른 연결" : "쓸 수 없는 연결"}
                            </option>
                          )}
                        </Select>
                      </div>
                    }
                  />
                </SettingCard>
              )}
            </>
          )}

          {/* 설정 페이지: 비식별화 */}
          {tab === "deident" && (
            <>
              {/*
                처리 순서가 비식별화, 재생본, 분석이라 메뉴에서 분석 연동 앞에 둔다.
                스위치 이름은 기능 이름이다.
                스위치 옆 설명은 켰을 때의 동작이다. 그래서 `켜면`으로 시작하지 않는다.
              */}
              {pageHead("비식별화", "다음에 등록하는 영상부터 적용")}
              <SettingCard aria-label="비식별화">
                <SettingRow
                  label="비식별화"
                  labelId="deident-enabled-label"
                  badge={changed("deident_enabled")}
                  description="새로 등록하는 영상의 얼굴을 분석 전에 이 서버에서 가리고, 분석과 작업 화면에 비식별화본을 씁니다."
                  control={
                    <Switch
                      checked={form.deident_enabled}
                      onChange={(on) => set("deident_enabled", on)}
                      labelledBy="deident-enabled-label"
                    />
                  }
                >
                  {/*
                    켜기 전에 처리한 영상은 자동으로 가려지지 않는다. 서버는 그 영상을 분석 요청에서 빼고,
                    영상 상세의 `비식별화 실행`으로 다시 처리할 수 있게 한다. 결과와 할 일은 두 줄로 나눈다.
                    버튼 이름은 영상 상세와 같게 쓴다.
                  */}
                  {form.deident_enabled && (
                    <InfoNote className="mt-3" title="켜기 전에 처리한 영상은 비식별화되지 않은 채 남고, 분석 요청에서 빠집니다.">
                      영상 상세의 ‘비식별화 실행’으로 한 편씩 처리합니다.
                    </InfoNote>
                  )}
                </SettingRow>
              </SettingCard>
            </>
          )}

          {/* 설정 페이지: 분석 연동 */}
          {/*
            머리에 사실 줄을 두지 않는다. 카드마다 저장 가능 여부가 다르다. 첫 영상을 등록하면 대상이 고정되고,
            분석 방식이 그 연동을 쓰지 않으면 카드에 그 사실만 남는다. 각 카드가 자기 상태를 안내한다.
          */}
          {tab === "link" && (
            <>
              {pageHead("분석 연동")}
              <ConnectionNotice project={project} stackHandled />
              {/* 저장소·분석 대상은 영상 등록 전에 저장해야 하는 값이라 맨 위에 둔다.
                  분석 대상 점검이 프로젝트 버킷과 워크스페이스 연동 버킷을 비교하므로 저장소를 먼저 둔다. */}
              <StorageTargetCard
                projectId={project.project_id}
                canManageConnections={globalRole === "super_admin"}
              />
              <AnalysisTargetCard
                projectId={project.project_id}
                canManageConnections={globalRole === "super_admin"}
              />
              <AnalysisProvisioningCard projectId={project.project_id} />
            </>
          )}

          {/* 설정 페이지: 위험 영역 */}
          {/* 서버는 삭제를 최고관리자에게만 허용한다(`soft_delete_project`). 메뉴도 그들에게만 이 절을 보여 준다. */}
          {tab === "danger" && canDelete && (
            <>
              {pageHead("위험 영역")}
              {/* 페이지 안의 비가역 동작은 위험 테두리 버튼이다. 채운 위험 버튼은 확인 창에만 둔다. */}
              <Card className="border-bad">
                <SettingRow
                  label="프로젝트 삭제"
                  description="목록에서 사라지고 멤버가 더는 들어오지 못합니다. 되돌릴 수 없습니다."
                  control={
                    <Button variant="danger-outline" onClick={() => setDeleteOpen(true)} className="shrink-0">
                      프로젝트 삭제
                    </Button>
                  }
                />
              </Card>
              {/* 삭제 대신 쓸 방법. 결과(굵게)와 안내(작게)를 두 줄로 나눈다. */}
              <InfoNote title="운영을 잠시 멈추려면 삭제 대신 상태를 바꿉니다.">
                <button
                  type="button"
                  onClick={() => setTab("general")}
                  className="font-semibold text-label underline underline-offset-2 hover:text-label-2"
                >
                  기본 정보의 상태
                </button>
                에서 일시정지나 종료를 고릅니다.
              </InfoNote>
            </>
          )}

          {/*
            저장 막대. 저장하지 않은 변경이 있을 때만 화면 아래에 붙는다.
            저장은 페이지와 관계없이 바뀐 필드를 모두 보낸다. 그래서 무엇이 바뀌었는지 이름으로 보여 준다.
            흑연색 막대로 띄워 폼과 구분한다. 막대 안의 글자·버튼은 흑연 값을 따른다.
          */}
          {isDirty && (
            <div
              role="region"
              aria-label="저장하지 않은 변경"
              data-surface="graphite"
              className="sticky bottom-4 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-card bg-panel px-5 py-3.5 shadow-e3"
            >
              <div className="min-w-0 flex-1">
                <p className="text-callout font-semibold text-label">저장하지 않은 변경 {changes.length}건</p>
                {blockedReason ? (
                  <p className="text-footnote text-bad-text">{blockedReason}</p>
                ) : (
                  <p className="truncate text-footnote text-label-2">{changeSummary(changes)}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button variant="secondary" size="sm" onClick={revert} disabled={save.isPending}>
                  되돌리기
                </Button>
                <Button size="sm" onClick={() => submit()} disabled={save.isPending || blockedReason != null}>
                  {save.isPending ? "저장 중…" : "저장"}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/*
        되돌릴 수 없는 동작이라 제목 줄에 `tone="danger"`와 아이콘을 둔다. 위험 신호를 본문 문장에만 맡기지 않는다.
        버튼 줄은 `footer`가 고정한다.
      */}
      <Modal
        open={deleteOpen}
        onClose={() => { setDeleteOpen(false); setConfirmName(""); }}
        title="프로젝트 삭제"
        icon={<TriangleAlert size={16} aria-hidden="true" />}
        tone="danger"
        footer={
          <>
            <Button variant="secondary" onClick={() => { setDeleteOpen(false); setConfirmName(""); }}>취소</Button>
            <DeleteProjectButton
              projectId={project.project_id}
              disabled={confirmName.trim() !== project.name}
              onDone={() => {
                toast("success", "프로젝트를 삭제했습니다");
                leavingRef.current = true; // 저장하지 않은 변경이 있어도 나가기 확인을 띄우지 않는다
                qc.invalidateQueries({ queryKey: projectKeys.list() });
                navigate("/");
              }}
            />
          </>
        }
      >
        {/*
          삭제하면 무엇이 되는지 확인창에 쓴다. 질문(`삭제하시겠습니까?`) 대신 결과 문장을 굵게, 근거를 작게 쓴다.
          서버는 `deleted_at`만 기록하는 soft delete다(`services/org/project.soft_delete_project`). 영상·클립·작업 기록은 남지만 화면에서 되살리는 기능은 없다.
          완전 삭제(hard delete)는 지원하지 않으므로 "90일 뒤 완전히 삭제됩니다"라고 쓰지 않는다.
        */}
        <p className="font-semibold text-label">‘{project.name}’ 프로젝트를 삭제합니다.</p>
        <p className="mt-1.5 text-footnote text-label-2">
          목록에서 사라지고 멤버가 더는 들어오지 못합니다. 영상·클립·작업 기록은 남지만 화면에서 되살리는 기능은 없습니다.
        </p>
        {deleteOpen && <DeleteImpact projectId={project.project_id} />}
        <div className="mt-4 space-y-1.5">
          <Label htmlFor="delete-confirm-name">확인을 위해 프로젝트 이름 입력</Label>
          <Input
            id="delete-confirm-name"
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            placeholder={project.name}
            autoComplete="off"
          />
        </div>
      </Modal>

      {/*
        저장하지 않고 앱 안에서 다른 화면으로 갈 때 확인 창을 띄운다. 무엇을 잃는지 이전·이후 표로 보여 주고 고르게 한다.
        고를 수 있는 것은 계속 편집, 저장하고 나가기, 나가기 셋이다. 버리는 쪽(나가기)은 왼쪽 위험색 글자 버튼으로 떼어 둔다.
        저장하고 나가기는 저장 막대와 같은 검사를 거친다. 막는 이유가 있으면 누를 수 없다.
        계속 편집을 고르면 되돌릴 수 있으므로 `danger` 대신 `warn`을 쓴다.
      */}
      <Modal
        open={blocker.state === "blocked"}
        onClose={() => { if (!save.isPending) blocker.reset?.(); }}
        title={`저장하지 않은 변경 ${changes.length}건`}
        icon={<TriangleAlert size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button
              variant="ghost"
              className="mr-auto text-bad-text hover:bg-bad-muted hover:text-bad-text"
              onClick={() => blocker.proceed?.()}
              disabled={save.isPending}
            >
              나가기
            </Button>
            <Button variant="secondary" onClick={() => blocker.reset?.()} disabled={save.isPending}>계속 편집</Button>
            <Button onClick={saveAndLeave} disabled={save.isPending || blockedReason != null}>
              {save.isPending ? "저장 중…" : "저장하고 나가기"}
            </Button>
          </>
        }
      >
        <p className="font-semibold text-label">나가면 이 변경은 사라집니다.</p>
        {/* 무엇이 사라지는지 설정마다 한 줄로 보여 준다. 건수만으로는 판단할 수 없다. */}
        <dl className="mt-3 divide-y divide-separator rounded-control bg-canvas-2 px-3 text-footnote">
          {changeItems.map((r) => (
            <div key={r.label} className="flex items-baseline justify-between gap-4 py-2">
              <dt className="text-label-2">{r.label}</dt>
              <dd className="text-right font-semibold tabular-nums text-label">
                {r.values ? `${r.values.from} → ${r.values.to}` : "변경"}
              </dd>
            </div>
          ))}
        </dl>
        {blockedReason && <p className="mt-3 text-footnote text-bad-text">{blockedReason}</p>}
      </Modal>
    </div>
  );
}

/** 작업 가이드의 여러 줄 입력란. 오른쪽 아래 모서리에 줄 수를 세어 가이드가 길어지는지 바로 보이게 한다. */
function CountedTextarea({
  count, className, ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { count: string }) {
  return (
    <div className={cn("relative", className)}>
      <textarea {...rest} className={cn(FIELD_BASE, "block resize-y px-3 pb-7 pt-2 leading-relaxed")} />
      <span className="pointer-events-none absolute bottom-2 right-3 text-caption tabular-nums text-label-3">{count}</span>
    </div>
  );
}

/**
 * 설정 절 안의 정보 상자. 결과(굵게) 한 줄과 할 일·이유(작게) 한 줄로 쓴다.
 * `warn`은 되돌릴 수 없는 결과를 알릴 때 쓴다.
 */
function InfoNote({
  title, children, tone = "neutral", className,
}: { title: ReactNode; children?: ReactNode; tone?: "neutral" | "warn"; className?: string }) {
  const warn = tone === "warn";
  const Icon = warn ? TriangleAlert : Info;
  return (
    <div
      role="note"
      className={cn(
        "flex gap-2.5 rounded-control px-4 py-3",
        warn ? "bg-warn-muted text-warn-text" : "border border-separator bg-canvas-2",
        className,
      )}
    >
      <Icon size={16} aria-hidden="true" className={cn("mt-0.5 shrink-0", !warn && "text-label-2")} />
      <div className="text-footnote">
        <p className={cn("font-semibold", !warn && "text-label")}>{title}</p>
        {children && <p className={cn("mt-0.5", !warn && "text-label-2")}>{children}</p>}
      </div>
    </div>
  );
}

/**
 * 프로젝트 삭제 확인 창의 영향 수(영상·클립·작업·멤버)를 그린다. 이름만 보고 지우지 않게 한다.
 *
 * 수는 서버 집계(`/projects/:id/stats`)다. 삭제는 최고관리자만 하고 최고관리자는 이 집계를 늘 받는다.
 */
function DeleteImpact({ projectId }: { projectId: string }) {
  const q = useQuery({
    queryKey: projectKeys.stats(projectId),
    queryFn: () => fetchProjectStats(projectId),
    meta: { handled: true },
  });
  const n = (x: number) => x.toLocaleString("ko-KR");
  if (q.isLoading) return <Skeleton className="mt-3 h-14" />;
  if (q.isError) {
    return (
      <p className="mt-3 rounded-card bg-canvas-2 px-3.5 py-3 text-footnote text-label-2">
        영향 수 조회 실패
      </p>
    );
  }
  const s = q.data!;
  return (
    <dl className="mt-3 grid grid-cols-4 gap-2 rounded-card bg-canvas-2 px-3.5 py-3 text-footnote">
      {[
        ["영상", `${n(s.total_videos)}개`],
        ["클립", `${n(s.total_clips)}개`],
        ["작업", `${n(s.total_work_items)}건`],
        ["멤버", `${n(s.total_members)}명`],
      ].map(([k, v]) => (
        <div key={k}>
          <dt className="text-caption text-label-3">{k}</dt>
          <dd className="font-semibold tabular-nums text-label">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** 프로젝트 삭제 확인창의 삭제 버튼. 실패하면 서버가 준 사유를 토스트로 알린다. */
function DeleteProjectButton({ projectId, onDone, disabled }: { projectId: string; onDone: () => void; disabled?: boolean }) {
  const { toast } = useToast();
  const mut = useMutation({
    mutationFn: () => deleteProject(projectId),
    onSuccess: onDone,
    // 실패하면 사유를 알린다. 버튼만 다시 활성화하면 실패했는지 알 수 없다.
    onError: (e) => failToast(toast, e, "삭제 실패"),
  });
  // 확인 버튼 이름에 무엇을 하는지 쓴다. `삭제`만 쓰면 창 제목을 다시 읽어야 한다.
  return (
    <Button variant="danger" onClick={() => mut.mutate()} disabled={mut.isPending || disabled}>
      {mut.isPending ? "삭제 중…" : "프로젝트 삭제"}
    </Button>
  );
}


/**
 * 분석 연동 상태 카드. 읽기 전용 진단을 화면을 열면 자동으로 불러 그린다.
 *
 * 진단(`GET`)은 벤더 계정에 아무것도 만들지 않는다. 워크스페이스와 분석 프로젝트는 운영자가
 * NCP 콘솔에서 만들고, 이 화면에서는 분석 대상 카드로 고르고 저장한다.
 *
 * 진단 목록은 공용 `DiagnosticsList`를 그대로 쓴다.
 * 같은 응답을 두 화면이 따로 그리면 판정 색과 문구가 달라진다.
 * 요약(`diagnosticsSummary`)은 카드 머리 오른쪽에 점과 함께 둔다. 정상 항목은 목록이 상자 없이 그린다.
 *
 * 분석 방식이 분석 서비스를 쓰지 않으면(샷 검출) 카드를 그리지 않는다. 판단은 서버가 주는
 * `supported`로 한다. 분석 대상 카드와 같은 조회라 캐시를 같이 쓴다. 서버도 그 프로젝트의
 * 진단을 422로 막는다.
 */
function AnalysisProvisioningCard({ projectId }: { projectId: string }) {
  const key = projectKeys.analysisProvisioning(projectId);

  const choices = useQuery({
    queryKey: projectKeys.analysisChoices(projectId),
    queryFn: () => fetchAnalysisChoices(projectId),
    enabled: !!projectId,
  });
  const supported = choices.data?.supported;
  const status = useQuery({
    queryKey: key,
    queryFn: () => fetchAnalysisProvisioning(projectId),
    enabled: !!projectId && supported === true,
  });

  if (supported === false) return null;
  const loading = choices.isPending || status.isLoading;
  const report = status.data;
  // 요약 점의 색. 실패가 있으면 위험, 주의·확인 전이 있으면 경고, 모두 정상이면 정상이다.
  const summaryTone = !report
    ? null
    : report.failed > 0
      ? "bad"
      : report.checks.every((c) => c.status === "ok") ? "ok" : "warn";
  return (
    <Card as="section" aria-label="연동 상태" className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <h3 className="text-callout font-semibold text-label">연동 상태</h3>
          <p className="mt-0.5 text-footnote text-label-2">분석 서비스 등록과 저장소 읽기 권한</p>
        </div>
        {report && summaryTone && (
          <span
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 text-footnote font-semibold tabular-nums",
              summaryTone === "ok" ? "text-ok-text" : summaryTone === "bad" ? "text-bad-text" : "text-warn-text",
            )}
          >
            <span
              aria-hidden="true"
              className={cn("size-2 rounded-capsule", summaryTone === "ok" ? "bg-ok" : summaryTone === "bad" ? "bg-bad" : "bg-warn")}
            />
            {diagnosticsSummary(report)}
          </span>
        )}
      </div>

      <div className="mt-3">
        {loading ? (
          // 불러오는 동안 문구 없이 골격만 그린다. 임시 글자가 결과처럼 읽히지 않게 한다.
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : report ? (
          <DiagnosticsList report={report} />
        ) : (
          /* 카드 안의 한 줄이라 `inline`으로 그린다. 다시 시도 버튼은 둔다. */
          <ErrorState
            inline
            message="상태 조회 실패"
            onRetry={() => void (choices.isError ? choices.refetch() : status.refetch())}
          />
        )}
      </div>

      <p className="mt-4 border-t border-separator pt-4 text-footnote text-label-2">
        워크스페이스와 분석 프로젝트는 NCP 콘솔에서 만든 뒤 분석 대상에서 고릅니다.
      </p>
    </Card>
  );
}
