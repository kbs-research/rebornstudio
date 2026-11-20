/**
 * 프로젝트 생성 화면(`/projects/new`).
 *
 * 한 단계 폼으로 주소 식별자(slug), 이름, 설명, 분석 방식만 받는다. 프로젝트는 서버가 기본 조직에 둔다.
 * super_admin만 들어온다. 프로젝트 목록과 전환기의 `프로젝트 추가`가 이 화면을 연다.
 *
 * 입력 실패는 해당 입력란 옆에 보여 준다.
 * 서버 검증 실패(422)는 어느 입력란인지 알려 준다(`fields`).
 * 주소 규칙은 계약(`ProjectCreateRequest.slug`)이 기준이고, 화면은 같은 식으로 먼저 거른다.
 * 두 규칙이 다르면 한쪽에서만 통과하는 입력이 생긴다.
 *
 * 폭 720px 한 열에 카드 둘(기본 정보, 분석 방식)을 둔다. 되돌아갈 링크(전체 프로젝트)를 제목 위에 둔다.
 * 주소 입력란에 `/p/` 접두를 붙여 입력란에서 결과 주소를 바로 볼 수 있다. 되돌릴 수 없는 두 선택(주소·분석 방식)에만
 * 잠금 표시와 `만든 뒤에는 바꿀 수 없습니다`를 단다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronLeft, Lock } from "lucide-react";

import { AccessDenied } from "@/shell/AccessDenied";
import { Button } from "@/shared/ui/Button";
import { Card, CardBody } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { FIELD_BASE } from "@/shared/ui/field";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { PageHeader } from "@/shared/ui/PageHeader";
import { SectionHeader } from "@/shared/ui/SectionHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { ApiError } from "@/shared/api/client";
import { errorText, type ErrorText } from "@/shared/api/error_text";
import { cn } from "@/shared/lib/cn";
import { useAuth } from "@/store/auth";
import type { components } from "@reborn/contracts";

import { createProject, fetchAnalysisPresets, projectKeys } from "../api";
import { NoConnectionNotice, type NoConnectionKind } from "../components/NoConnectionNotice";

/**
 * 여러 줄 입력란의 클래스. 높이만 `rows`가 정하고 나머지는 `FIELD_BASE`를 그대로 쓴다.
 *
 * 복사한 클래스는 원본이 바뀌어도 따라 바뀌지 않아 한 화면 안에서 배경색, 호버, 포커스가 달라진다.
 */
const TEXTAREA_CLS = cn(FIELD_BASE, "resize-y px-3 py-2");

/**
 * 프로젝트 주소 규칙. 계약(`ProjectCreateRequest.slug`)의 규칙을 옮긴 사본이다.
 * 3~50자, 영문 소문자·숫자·하이픈이고, 첫 글자는 영문 소문자나 숫자다.
 * 서버가 기준이므로, 화면 검사를 통과해도 서버가 거르면 그 결과를 입력란 옆에 보여 준다.
 */
const SLUG_RE = /^[a-z0-9][a-z0-9-]{2,49}$/;
const SLUG_RULE = "영문 소문자·숫자·하이픈 3~50자, 첫 글자는 영문 소문자나 숫자";

/*
  요청 타입은 직접 적지 않고 계약에서 생성된 타입을 쓴다. 그래야 계약과 폼이 어긋나지 않는다.
  복사본을 두면 계약에 필드가 추가돼도 폼이 보내지 않아, 서버가 알리지 않고 기본값을 쓴다.
*/
type ProjectCreateRequest = components["schemas"]["ProjectCreateRequest"];
/** 422 필드 오류. `api()`가 Pydantic 오류를 `fields: [{path, message}]`로 바꿔 던진다. */
type FieldApiError = ApiError & { fields?: { path: string; message: string }[] };
type FieldKey = "slug" | "name" | "description";

/**
 * 서버가 가리킨 입력란별로 옆에 보여 줄 문구.
 * 서버 원문(영문)은 보여 주지 않고 무엇을 넣어야 하는지 안내한다.
 */
const FIELD_HINT: Record<FieldKey, ErrorText> = {
  slug: { message: "프로젝트 주소 형식이 맞지 않습니다", hint: SLUG_RULE },
  name: { message: "프로젝트 이름을 확인해주세요", hint: "1~100자로 입력해주세요." },
  description: { message: "설명을 확인해주세요" },
};

interface ProjectDTO {
  project_id: string;
  slug: string;
  name: string;
}

/**
 * 입력란 아래에 오류를 두 줄(결과, 안내)로 보여 준다. 오류가 없으면 공간을 차지하지 않는다.
 * 조회 실패 대신 입력 검사 결과를 보여 준다.
 *
 * 위쪽 간격은 래퍼(`space-y-1.5`)가 준다. 이 컴포넌트에 `mt-`를 더 붙이면 위치마다 간격이 달라진다.
 */
function FieldError({ text }: { text?: ErrorText }): ReactNode {
  if (!text) return null;
  return (
    <div role="alert">
      <p className="text-footnote text-bad-text">{text.message}</p>
      {text.hint && <p className="text-footnote text-label-3">{text.hint}</p>}
    </div>
  );
}

/** 되돌릴 수 없는 선택 옆의 잠금 한 줄을 그린다. */
function Irreversible() {
  return (
    <p className="flex items-center gap-1.5 text-footnote font-semibold text-label">
      <Lock size={14} aria-hidden="true" />
      만든 뒤에는 바꿀 수 없습니다.
    </p>
  );
}

/** 프로젝트 생성 폼을 그린다. 만들면 새 프로젝트 홈으로 이동한다. */
export function ProjectNewPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const user = useAuth((s) => s.user);
  const canCreate = user?.global_role === "super_admin";

  /*
    분석 스택 카탈로그. 쓸 수 없는 스택도 이유와 함께 온다.
    목록에서 빼면 관리자가 "지원하지 않는 것"과 "아직 준비되지 않은 것"을 구분할 수 없다.
  */
  const presets = useQuery({
    queryKey: projectKeys.analysisPresets(),
    queryFn: fetchAnalysisPresets,
  });

  const [form, setForm] = useState({
    slug: "",
    name: "",
    description: "",
  });
  /*
    고르지 않으면 서버가 기본 조합을 쓰지만, 화면은 기본값을 미리 고른 상태로 보여 준다. 되돌릴 수 없는 선택을 결과를 알지 못한 채 넘기지 않게 한다.
    어느 것이 기본인지는 서버가 알려 준다(`is_default`). 키를 화면에 적으면 서버가 기본값을 바꿨을 때 폼만 따라가지 못한다.
    기본값을 이 서버에서 쓸 수 없으면 미리 고르지 않는다.
    고를 수 없는 카드가 골라진 채 남으면 빈 원과 "선택됨"이 서로 맞지 않는다.
  */
  const [preset, setPreset] = useState<string | null>(null);
  const defaultPreset = presets.data?.find((p) => p.is_default);
  const chosen = preset ?? (defaultPreset?.available ? defaultPreset.key : null);
  /*
    추가할 수 없으면 버튼을 막고 그 이유를 버튼 아래에 쓴다. 누르게 두면 서버가 거절한 뒤에야 이유를 알게 된다.
    쓸 수 있는지와 이유는 서버(`presets.readiness`)가 정한다. 고른 방식을 쓸 수 없으면 그 이유를 쓴다.
    고르지 않았는데 기본만 막혔으면 다른 방식을 고르라고 쓴다(고르지 않으면 서버가 막힌 기본을 쓴다).
    쓸 수 있는 방식이 하나도 없으면 첫 항목의 이유를 쓴다. 각 카드는 자기 막힌 이유를 따로 보여 준다.
  */
  const chosenPreset = presets.data?.find((p) => p.key === chosen);
  const anyAvailable = !!presets.data?.some((p) => p.available);
  const presetBlocked: { reason: string; hint?: string } | null = chosenPreset
    ? (chosenPreset.available ? null : { reason: chosenPreset.unavailable_reason ?? "이 서버에서 쓸 수 없는 방식입니다." })
    : !presets.data
      ? null
      : anyAvailable
        ? { reason: "기본 방식을 이 서버에서 쓸 수 없습니다.", hint: "쓸 수 있는 방식을 고르세요." }
        : presets.data[0]?.unavailable_reason
          ? { reason: presets.data[0].unavailable_reason }
          : null;
  /*
    등록한 연결이 없어 막힌 방식이 있으면 무엇을 어디서 등록하는지 폼 위에 한 번 알린다.
    어느 연결이 없는지는 서버가 방식마다 준다(`missing_providers`). 미리 채워 두는 기본 연결은 없다.
  */
  const missing = new Set((presets.data ?? []).flatMap((p) => p.missing_providers ?? []));
  const missingKind: NoConnectionKind | null = missing.has("ncp") && missing.has("maiu")
    ? "both" : missing.has("ncp") ? "storage" : missing.has("maiu") ? "analysis" : null;
  const [error, setError] = useState<ErrorText | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldKey, ErrorText>>>({});

  // 입력하는 동안 규칙에 맞지 않으면 규칙 줄을 오류 색으로 바꾼다. 제출한 뒤에야 알게 되지 않게 한다.
  const slugInvalid = form.slug !== "" && !SLUG_RE.test(form.slug);

  const mut = useMutation<ProjectDTO, FieldApiError, ProjectCreateRequest>({
    mutationFn: (body: ProjectCreateRequest) => createProject(body),
    onSuccess: (proj) => {
      qc.invalidateQueries({ queryKey: projectKeys.list() });
      navigate(`/p/${proj.slug}`);
    },
    // `api()`가 던지는 것은 Error가 아니라 `{ status, detail }`이므로 `detail`을 읽는다.
    // `e.message`를 읽으면 값이 늘 undefined라 `slug_already_exists`("이미 쓰이는 주소입니다")가 화면에 나오지 않는다.
    onError: (e) => {
      // 서버가 입력란을 가리켰으면(422) 그 입력란 옆에 보여 준다.
      // 주소 중복(409)도 주소 입력란에 보여 준다.
      const byField: Partial<Record<FieldKey, ErrorText>> = {};
      for (const f of e?.fields ?? []) {
        const key = f.path.split(".").pop() as FieldKey;
        if (key in FIELD_HINT) byField[key] = FIELD_HINT[key];
      }
      if (e?.detail === "slug_already_exists") byField.slug = errorText(e.detail);
      setFieldErrors(byField);
      setError(
        Object.keys(byField).length
          ? null
          : errorText(e?.detail, { message: "프로젝트 추가 실패", hint: "잠시 후 다시 시도해주세요." }),
      );
    },
  });

  /**
   * 폼을 제출한다. 필수 입력과 주소 규칙을 먼저 검사한 뒤 mutation을 실행한다.
   * 걸린 입력란은 그 옆에 안내한다.
   */
  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const errs: Partial<Record<FieldKey, ErrorText>> = {};
    // 비어 있으면 형식 오류 대신 입력 누락으로 본다. 이름 입력란과 같은 문구로 알린다.
    if (!form.slug) errs.slug = { message: "프로젝트 주소를 입력해주세요", hint: SLUG_RULE };
    else if (!SLUG_RE.test(form.slug)) errs.slug = FIELD_HINT.slug;
    if (!form.name.trim()) errs.name = { message: "프로젝트 이름을 입력해주세요" };
    setFieldErrors(errs);
    if (Object.keys(errs).length || presetBlocked) return;
    mut.mutate({
      ...form,
      description: form.description.trim() || undefined,
      // 목록을 아직 받지 못했으면 보내지 않는다. 그러면 서버가 기본값을 쓴다.
      analysis_preset: chosen ?? undefined,
    });
  }

  /** 필드 하나의 onChange 핸들러를 만든다. 값을 바꾸면 그 입력란의 오류 표시를 지운다. */
  function field<K extends keyof typeof form>(key: K) {
    return (e: { target: { value: string } }) => {
      setForm((s) => ({ ...s, [key]: e.target.value }));
      setFieldErrors((fe) => (fe[key] ? { ...fe, [key]: undefined } : fe));
    };
  }

  // 라우터(`RoleGuard`)가 이미 막으므로 이 분기에 오는 일은 드물다.
  // 공용 패널을 쓰고, 막힌 까닭은 라우터와 같은 `global`로 쓴다.
  if (!canCreate) {
    return (
      <AccessDenied
        reason="global"
        message="프로젝트는 최고관리자만 추가할 수 있습니다."
        hint="필요하면 최고관리자에게 요청해주세요."
      />
    );
  }

  return (
    <div className="max-w-[720px]">
      <Link
        to="/projects"
        className="mb-3 inline-flex items-center gap-1 rounded-control text-footnote text-label-2 hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        <ChevronLeft size={14} aria-hidden="true" />
        전체 프로젝트
      </Link>
      <PageHeader title="프로젝트 추가" subtitle="만든 사람이 이 프로젝트의 관리자가 됩니다." />
      {/*
        등록한 연결이 없으면 이름을 입력하기 전에 먼저 알린다. 방식 카드 아래에만 두면 폼을 다 채운 뒤에야 막힌 이유를 본다.
      */}
      {missingKind && (
        <div className="mb-4">
          <NoConnectionNotice kind={missingKind} canManage={user?.global_role === "super_admin"} />
        </div>
      )}

      <form onSubmit={onSubmit} noValidate>
        {/*
          섹션 1: 기본 정보.
          카드, 머리, 본문을 공용 부품으로 그린다. 클래스를 복사하면 부품이 바뀌어도 이 화면만 따라 바뀌지 않는다.
          `SectionHeader`는 제목을 `text-callout`(14px)으로 그린다.
        */}
        <Card>
          <SectionHeader title="기본 정보" />
          <CardBody className="space-y-5">
            {/*
              라벨, 입력, 도움말 사이 간격은 래퍼 하나(`space-y-1.5`)가 정한다.
              요소마다 `mt-*`를 붙이면 한 입력란 안에서도 간격이 달라진다.
              사람이 먼저 떠올리는 것은 이름이라 이름을 먼저 받고 주소를 다음에 받는다.
            */}
            <div className="space-y-1.5">
              <Label htmlFor="name" required>프로젝트 이름</Label>
              <Input
                id="name" value={form.name} onChange={field("name")}
                placeholder="예: 저녁 뉴스 2026 가공" required
                aria-invalid={fieldErrors.name ? true : undefined}
              />
              <FieldError text={fieldErrors.name} />
            </div>
            <div className="space-y-1.5">
              {/*
                라벨과 설명에 개발자 용어(`slug`)를 쓰지 않는다.
                `주소 식별자`는 만든 말이라 `프로젝트 주소`로 부르고, 입력 아래에 결과 주소를
                미리 보여 준다.
              */}
              <Label htmlFor="slug" required>프로젝트 주소</Label>
              {/* 입력란 자체가 결과 주소를 보여 주므로 별도 미리보기 줄을 두지 않는다. */}
              <div
                className={cn(
                  "flex h-[38px] items-stretch overflow-hidden rounded-control border bg-panel focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25",
                  fieldErrors.slug || slugInvalid ? "border-bad" : "border-separator-strong",
                )}
              >
                <span aria-hidden="true" className="flex items-center border-r border-separator bg-canvas-2 px-3 font-mono text-footnote text-label-2">
                  /p/
                </span>
                <input
                  id="slug"
                  value={form.slug}
                  onChange={field("slug")}
                  placeholder="news-2026"
                  required
                  aria-invalid={fieldErrors.slug || slugInvalid ? true : undefined}
                  aria-describedby="slug-help"
                  className="min-w-0 flex-1 bg-transparent px-3 font-mono text-callout text-label outline-none placeholder:text-placeholder"
                />
              </div>
              {fieldErrors.slug ? (
                <FieldError text={fieldErrors.slug} />
              ) : (
                /* 규칙과 되돌릴 수 없다는 사실을 한 문단에 섞지 않는다. 사실은 잠금 줄로 따로 둔다. */
                <div id="slug-help" className="space-y-1">
                  <p className={cn("text-footnote", slugInvalid ? "text-bad-text" : "text-label-2")}>{SLUG_RULE}</p>
                  <Irreversible />
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="description">설명</Label>
              <textarea
                id="description" value={form.description} onChange={field("description")}
                rows={3} placeholder="예: 2026년 보도영상 캡션 가공" className={TEXTAREA_CLS}
              />
              <FieldError text={fieldErrors.description} />
            </div>
          </CardBody>
        </Card>

        {/* 섹션 2: 분석 스택 */}
        <Card className="mt-4">
          <SectionHeader title="분석 방식" />
          <CardBody className="space-y-3">
            {/*
              계약이 화면에 알리도록 정한 문구는 "만들 때 정해지며 나중에 바꿀 수 없습니다"이다.
              되돌릴 수 없는 선택을 알리지 않고 정하지 않는다.
            */}
            <Irreversible />

            {presets.isLoading && (
              <div role="status" aria-label="분석 방식 불러오는 중" className="space-y-2">
                <Skeleton className="h-16 w-full rounded-control" />
                <Skeleton className="h-16 w-full rounded-control" />
              </div>
            )}
            {presets.isError && (
              <ErrorState
                inline
                message="분석 방식 목록 조회 실패"
                description="고르지 않고 만들면 기본 조합으로 정해집니다."
                onRetry={() => void presets.refetch()}
              />
            )}

            {/*
              하나만 고르는 목록이므로 라디오 역할을 준다.
              일반 button으로 두면 접근성 트리에 `button`만 남아 무엇을 고르는 목록인지,
              지금 무엇이 선택됐는지 읽히지 않는다.
            */}
            <div role="radiogroup" aria-label="분석 방식" className="space-y-2">
            {presets.data?.map((p) => {
              const on = chosen === p.key;
              return (
                <button
                  key={p.key}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  aria-label={p.label}
                  disabled={!p.available}
                  onClick={() => setPreset(p.key)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-card border px-4 py-3.5 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                    !p.available
                      ? "cursor-not-allowed border-separator"
                      : on
                        ? "border-accent ring-1 ring-accent"
                        : "border-separator-strong hover:bg-canvas-2",
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-capsule border-[1.5px]",
                      on && p.available ? "border-accent" : "border-separator-strong",
                    )}
                    aria-hidden="true"
                  >
                    {on && p.available && <span className="size-2 rounded-capsule bg-accent" />}
                  </span>
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className={cn("text-callout font-semibold", p.available ? "text-label" : "text-label-2")}>{p.label}</span>
                      {/*
                        "지금 운영 중"은 고를 때 쓸모 있는 사실이라
                        설명 문장에 넣지 않고 배지로 표시한다.
                      */}
                      {p.is_default && (
                        <span className="rounded-control bg-canvas-2 px-1.5 py-0.5 text-caption font-semibold text-label-2">
                          기본
                        </span>
                      )}
                    </span>
                    {p.description && (
                      <span className="mt-1 block text-footnote text-label-2">{p.description}</span>
                    )}
                    {/*
                      쓸 수 없는 항목에는 반드시 이유를 보여 준다.
                      이유 없이 비활성이면 사용자가 다음에 할 일을 알 수 없다.
                    */}
                    {!p.available && p.unavailable_reason && (
                      <span className="mt-1.5 flex items-center gap-1 text-caption text-label-2">
                        <Lock size={12} aria-hidden="true" />
                        {p.unavailable_reason}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
            </div>
          </CardBody>
        </Card>

        {/* 입력란에 속하지 않는 오류와 하단 버튼 */}
        {error && (
          <div role="alert" className="mt-3 rounded-field border border-bad/30 bg-bad-muted px-4 py-2.5 text-bad-text">
            <p className="text-callout">{error.message}</p>
            {error.hint && <p className="mt-0.5 text-footnote text-label-2">{error.hint}</p>}
          </div>
        )}

        {/* 취소·추가는 폼 끝 오른쪽에 둔다. 주 버튼은 추가 하나다. */}
        <div className="mt-5 flex items-center justify-end gap-2">
          <Button type="button" variant="secondary" onClick={() => navigate("/projects")}>
            취소
          </Button>
          <Button
            type="submit"
            disabled={mut.isPending || !!presetBlocked}
            aria-describedby={presetBlocked ? "create-blocked" : undefined}
          >
            {mut.isPending ? "추가 중…" : "추가"}
          </Button>
        </div>
        {/* 누를 수 없는 이유는 버튼 바로 아래에 쓴다. 위쪽 카드에만 두면 버튼 앞에서 보이지 않는다. */}
        {presetBlocked && (
          <div id="create-blocked" className="mt-2 text-right">
            <p className="text-footnote font-semibold text-label">추가할 수 없음</p>
            <p className="text-footnote text-label-2">{presetBlocked.reason}</p>
            {presetBlocked.hint && <p className="text-footnote text-label-2">{presetBlocked.hint}</p>}
          </div>
        )}
      </form>
    </div>
  );
}
